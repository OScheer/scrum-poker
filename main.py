import json

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect, Query
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
import uvicorn
from jinja2 import Environment, FileSystemLoader

from app.room_manager import RoomManager, Phase, Story, _generate_id, CARD_VALUES


room_manager = RoomManager()
room_connections: dict[str, set[WebSocket]] = {}


async def broadcast_json(room_id: str, msg: dict):
    connections = room_connections.get(room_id, set())
    payload = json.dumps(msg)
    dead = set()
    for ws in connections:
        try:
            await ws.send_text(payload)
        except Exception:
            dead.add(ws)
    connections -= dead


async def broadcast_room_state(room_id: str):
    room = room_manager.get_room(room_id)
    if not room:
        return
    state = room.to_public_dict()
    await broadcast_json(room_id, {"type": "state", **state})


app = FastAPI(title="Scrum Poker")
jinja_env = Environment(
    loader=FileSystemLoader("app/templates"),
    autoescape=True,
)
app.mount("/static", StaticFiles(directory="static"), name="static")


def render(name: str, context: dict, status_code: int = 200) -> HTMLResponse:
    template = jinja_env.get_template(name)
    html = template.render(**context)
    return HTMLResponse(html, status_code=status_code)


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return render("index.html", {"request": request, "card_values": CARD_VALUES})


@app.post("/create")
async def create_room(request: Request):
    data = await request.json()
    room_name = data.get("room_name", "Scrum Poker").strip()
    admin_name = data.get("user_name", "Admin").strip()
    if not room_name:
        room_name = "Scrum Poker"
    if not admin_name:
        admin_name = "Admin"
    room_id, admin_id = room_manager.create_room(room_name, admin_name)
    return {
        "room_id": room_id,
        "user_id": admin_id,
        "redirect": f"/room/{room_id}?user_id={admin_id}",
    }


@app.post("/join")
async def join_room(request: Request):
    data = await request.json()
    room_id = data.get("room_id", "").strip().upper()
    user_name = data.get("user_name", "Anonymous").strip()
    if not user_name:
        user_name = "Anonymous"

    room = room_manager.get_room(room_id)
    if not room:
        return {"error": "Raum nicht gefunden"}

    user_id = room_manager.add_user(room_id, user_name)
    if not user_id:
        return {"error": "Raum nicht gefunden"}

    return {
        "room_id": room_id,
        "user_id": user_id,
        "redirect": f"/room/{room_id}?user_id={user_id}",
    }


@app.get("/room/{room_id}", response_class=HTMLResponse)
async def room_page(request: Request, room_id: str, user_id: str | None = Query(None)):
    room = room_manager.get_room(room_id)
    if not room:
        return RedirectResponse(url="/")

    if not user_id:
        return HTMLResponse(f"""<!DOCTYPE html>
<html lang="de">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Raum beitreten — Scrum Poker</title>
<link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet">
<link href="/static/style.css" rel="stylesheet">
<style>body{{background:#1a1d23;color:#e0e0e0}}</style>
</head>
<body class="d-flex align-items-center min-vh-100">
<div class="container">
<div class="row justify-content-center">
<div class="col-md-6">
<div class="card bg-dark border-secondary shadow-lg">
<div class="card-body text-center">
<h3 class="text-white mb-3">Raum <strong>{room_id}</strong></h3>
<p class="text-secondary mb-4">Gib deinen Namen ein, um beizutreten</p>
<form onsubmit="join(event)">
<div class="mb-3">
<input id="name-input" class="form-control bg-dark text-white border-secondary" placeholder="Dein Name" required autofocus>
</div>
<button type="submit" class="btn btn-primary w-100">Beitreten</button>
<div id="error-msg" class="alert alert-danger mt-3 d-none"></div>
</form>
</div></div></div></div></div>
<script>
async function join(e) {{
    e.preventDefault();
    const name = document.getElementById('name-input').value;
    const err = document.getElementById('error-msg');
    err.classList.add('d-none');
    const resp = await fetch('/join', {{
        method:'POST',
        headers:{{'Content-Type':'application/json'}},
        body: JSON.stringify({{room_id:'{room_id}',user_name:name}})
    }});
    const data = await resp.json();
    if (data.error) {{
        err.textContent = data.error;
        err.classList.remove('d-none');
    }} else if (data.redirect) {{
        window.location.href = data.redirect;
    }}
}}
</script>
</body>
</html>""")

    user = room.get_user_by_id(user_id)
    if not user:
        return RedirectResponse(url=f"/room/{room_id}")

    return render("room.html", {
        "request": request,
        "room": room,
        "user": user,
        "card_values": CARD_VALUES,
    })


@app.websocket("/ws/{room_id}")
async def websocket_endpoint(
    websocket: WebSocket, room_id: str, user_id: str = Query(...)
):
    room = room_manager.get_room(room_id)
    if not room:
        await websocket.close(code=4004, reason="Room not found")
        return

    user = room.get_user_by_id(user_id)
    if not user:
        await websocket.close(code=4004, reason="User not found")
        return

    await websocket.accept()

    if room_id not in room_connections:
        room_connections[room_id] = set()
    room_connections[room_id].add(websocket)

    await websocket.send_json({"type": "state", **room.to_public_dict()})

    await broadcast_room_state(room_id)

    try:
        while True:
            data = await websocket.receive_json()
            msg_type = data.get("type", "")

            if msg_type == "vote":
                value = data.get("value")
                async with room.lock:
                    if value is None:
                        room.votes.pop(user_id, None)
                    else:
                        room.votes[user_id] = value
                await broadcast_room_state(room_id)

            elif msg_type == "reveal":
                if room.is_admin(user_id):
                    async with room.lock:
                        room.phase = Phase.REVEALED
                    await broadcast_room_state(room_id)

            elif msg_type == "reset":
                if room.is_admin(user_id):
                    async with room.lock:
                        room.votes = {}
                        room.phase = Phase.VOTING
                    await broadcast_room_state(room_id)

            elif msg_type == "add_story":
                if room.is_admin(user_id):
                    title = data.get("title", "").strip()
                    if not title:
                        continue
                    description = data.get("description", "").strip()
                    async with room.lock:
                        story = Story(
                            id=_generate_id(),
                            title=title,
                            description=description,
                            order=len(room.stories),
                        )
                        room.stories.append(story)
                    await broadcast_room_state(room_id)

            elif msg_type == "select_story":
                if room.is_admin(user_id):
                    story_id = data.get("story_id", "")
                    async with room.lock:
                        for i, s in enumerate(room.stories):
                            if s.id == story_id:
                                room.current_story_idx = i
                                room.votes = {}
                                room.phase = Phase.VOTING
                                break
                    await broadcast_room_state(room_id)

            elif msg_type == "delete_story":
                if room.is_admin(user_id):
                    story_id = data.get("story_id", "")
                    async with room.lock:
                        for i, s in enumerate(room.stories):
                            if s.id == story_id:
                                room.stories.pop(i)
                                if room.current_story_idx >= len(room.stories):
                                    room.current_story_idx = len(room.stories) - 1
                                break
                    await broadcast_room_state(room_id)

            elif msg_type == "throw_paper":
                target_id = data.get("target_id", "")
                emoji = data.get("emoji", "📄")
                target_name = "jemanden"
                if target_id:
                    target_user = room.get_user_by_id(target_id)
                    if target_user:
                        target_name = target_user.name
                current_user = room.get_user_by_id(user_id)
                from_name = current_user.name if current_user else "Jemand"
                await broadcast_json(room_id, {
                    "type": "paper_ball",
                    "from": from_name,
                    "target": target_name,
                    "emoji": emoji,
                })

    except WebSocketDisconnect:
        pass
    except Exception as e:
        print(f"WebSocket error: {e}")
    finally:
        connections = room_connections.get(room_id)
        if connections:
            connections.discard(websocket)

        room_manager.remove_user(room_id, user_id)
        room = room_manager.get_room(room_id)

        if room:
            if user_id == room.admin_id and room.users:
                new_admin = next(iter(room.users.values()))
                async with room.lock:
                    new_admin.is_admin = True
                    room.admin_id = new_admin.id

            await broadcast_room_state(room_id)

        if room_id in room_connections and not room_connections[room_id]:
            del room_connections[room_id]


if __name__ == "__main__":
    import os
    port = int(os.getenv("PORT", "8000"))
    uvicorn.run("main:app", host="0.0.0.0", port=port)

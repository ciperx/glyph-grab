#!/usr/bin/env python3
"""
GLYPH GRAB — a 2-4 player turn-based multiplayer strategy game
playable by humans (browser) and AI agents (HTTP/JSON API) in the same match.

Zero dependencies: Python stdlib only (http.server). In-memory match state.

Run: python3 glyph_game.py [--port 8042]
API  : see README section at bottom / GET /api/docs
"""
import json, random, re, threading, time, urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = 8042
N = 7                 # N x N board
MAX_TURNS = 60        # stalemate safeguard (total moves before majority-decide)
WIN_THRESHOLD = N*N // 2 + 1   # > half the cells => instant dominance win

COLORS = ["#e94560", "#22a2f0", "#f5b642", "#7c5cff"]  # player 0..3

# ---------------------------------------------------------------------------
# Game logic
# ---------------------------------------------------------------------------
class Game:
    def __init__(self, code, num_players):
        self.code = code
        self.num_players = num_players
        self.board = [[None]*N for _ in range(N)]   # None or player index 0..3
        self.players = []          # list of dicts {id, name, type, color}
        self.turn = 0              # index into players
        self.turn_count = 0
        self.log = []              # list of move dicts
        self.winner = None
        self.created = time.time()
        # seed corners
        corners = [(0,0),(0,N-1),(N-1,N-1),(N-1,0)]
        for p in range(num_players):
            r,c = corners[p]
            self.board[r][c] = p

    def join(self, name, ptype, bot_strength=0):
        if len(self.players) >= self.num_players:
            return None
        pid = len(self.players)
        self.players.append({
            "id": pid, "name": name, "type": ptype, "color": COLORS[pid],
            "strength": bot_strength, "cells": 1,
        })
        return pid

    def cells_of(self, p):
        return sum(1 for r in range(N) for c in range(N) if self.board[r][c] == p)

    def adj(self, r, c):
        return [(r+dr, c+dc) for dr,dc in ((1,0),(-1,0),(0,1),(0,-1))
                if 0 <= r+dr < N and 0 <= c+dc < N]

    def legal_moves(self, p):
        """Return list of moves: {action, row, col, flips}."""
        moves = []
        # SEIZE: capture enemy cell orth-adjacent to my cells where I outnumber their support
        for r in range(N):
            for c in range(N):
                if self.board[r][c] is not None and self.board[r][c] != p:
                    if any(self.board[ar][ac] == p for ar,ac in self.adj(r,c)):
                        mine  = sum(1 for ar,ac in self.adj(r,c) if self.board[ar][ac] == p)
                        theirs = sum(1 for ar,ac in self.adj(r,c) if self.board[ar][ac] == self.board[r][c])
                        if mine > theirs:
                            moves.append({"action":"seize","row":r,"col":c,"flips":1})
        # SPREAD: claim empty cell orth-adjacent to my cells
        for r in range(N):
            for c in range(N):
                if self.board[r][c] is None:
                    if any(self.board[ar][ac] == p for ar,ac in self.adj(r,c)):
                        moves.append({"action":"spread","row":r,"col":c,"flips":0})
        return moves

    def apply(self, p, action, row, col):
        if self.winner is not None:
            return {"error": "game over"}
        if p != self.turn:
            return {"error": "not your turn"}
        mv = {"action": action, "row": row, "col": col,
              "player": p, "name": self.players[p]["name"]}
        target = self.board[row][col]
        if action == "spread":
            if 0 <= row < N and 0 <= col < N and target is None:
                self.board[row][col] = p
            else:
                return {"error": "illegal spread"}
        elif action == "seize":
            if 0 <= row < N and 0 <= col < N and target is not None and target != p:
                mine   = sum(1 for ar,ac in self.adj(row,col) if self.board[ar][ac] == p)
                theirs = sum(1 for ar,ac in self.adj(row,col) if self.board[ar][ac] == target)
                if mine > theirs and any(self.board[ar][ac] == p for ar,ac in self.adj(row,col)):
                    self.board[row][col] = p
                else:
                    return {"error": "illegal seize (not outnumbering)"}
            else:
                return {"error": "illegal seize"}
        else:
            return {"error": "unknown action"}
        self.log.append(mv)
        self.turn_count += 1
        # check win
        counts = [self.cells_of(i) for i in range(self.num_players)]
        if counts[self.turn] >= WIN_THRESHOLD:
            self.winner = self.turn
        elif self._full():
            self.winner = counts.index(max(counts))
        elif self.turn_count >= MAX_TURNS:
            self.winner = counts.index(max(counts))
        # advance turn, skipping any player with no legal move (stalemate safety)
        self.advance()
        return {"ok": True, "state": self.state()}

    def advance(self):
        # move token to the next player who has a legal move; if everybody is
        # stuck, decide by majority (board can deadlock with trapped cells).
        for _ in range(self.num_players):
            self.turn = (self.turn + 1) % self.num_players
            if self.winner is not None or self.legal_moves(self.turn):
                return
        counts = [self.cells_of(i) for i in range(self.num_players)]
        self.winner = counts.index(max(counts))

    def _full(self):
        return all(self.board[r][c] is not None for r in range(N) for c in range(N))

    def state(self):
        return {
            "code": self.code,
            "n": N,
            "board": self.board,
            "turn": self.turn,
            "turn_of": self.players[self.turn]["name"] if self.players else None,
            "turn_count": self.turn_count,
            "max_turns": MAX_TURNS,
            "winner": self.winner,
            "players": [{"id": pp["id"], "name": pp["name"], "type": pp["type"],
                         "color": pp["color"], "cells": self.cells_of(pp["id"])}
                        for pp in self.players],
            "log": self.log[-40:],
        }

# ---------------------------------------------------------------------------
# Sample bot (greedy + light randomness) — the "basic sample bot" and the
# auto-opponent when a seat is empty.
# ---------------------------------------------------------------------------
def bot_choose(game, p):
    moves = game.legal_moves(p)
    if not moves:
        return None
    def score(mv):
        # simulate
        import copy
        b = copy.deepcopy(game.board)
        if mv["action"] == "seize":
            b[mv["row"]][mv["col"]] = p
        else:
            b[mv["row"]][mv["col"]] = p
        cells = sum(1 for r in range(N) for c in range(N) if b[r][c] == p)
        # preference: seize > spread; small noise for variety
        return cells + (0.6 if mv["action"] == "seize" else 0) + random.random()*0.3
    return max(moves, key=score)

# ---------------------------------------------------------------------------
# Match registry
# ---------------------------------------------------------------------------
GAMES = {}
def new_code():
    while True:
        c = "".join(random.choices("ABCDEFGHJKMNPQRSTUVWXYZ23456789", k=4))
        if c not in GAMES:
            return c

# ---------------------------------------------------------------------------
# HTTP server
# ---------------------------------------------------------------------------
INDEX = r"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Glyph Grab — multiplayer strategy</title>
<style>
:root{--bg:#0f1220;--panel:#1a1e32;--fg:#eef;--dim:#8b92b8}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);
 font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif;min-height:100vh}
.wrap{max-width:560px;margin:0 auto;padding:20px}
h1{font-size:1.4rem;margin:.2em 0}
h1 span{color:#f5b642}
.sub{color:var(--dim);font-size:.85rem;margin-bottom:1em}
.panel{background:var(--panel);border:1px solid #262c4a;border-radius:14px;padding:16px;margin-bottom:16px}
label{display:block;font-size:.8rem;color:var(--dim);margin-bottom:6px}
input,select,button{width:100%;padding:11px;border-radius:9px;border:1px solid #2b3358;
 background:#10142a;color:var(--fg);font-size:.95rem;margin-bottom:10px}
button{background:#22a2f0;color:#04101c;font-weight:700;cursor:pointer;border:none}
button.ghost{background:#2b3358;color:var(--fg)}
.grid{display:grid;grid-template-columns:repeat(7,1fr);gap:4px}
.cell{aspect-ratio:1;border-radius:7px;background:#10142a;border:1px solid #262c4a;cursor:pointer;
 display:flex;align-items:center;justify-content:center;font-size:.7rem;color:var(--dim);transition:.12s}
.cell.empty:hover{background:#232a52}
.cell.p0{background:#e94560}.cell.p1{background:#22a2f0}.cell.p2{background:#f5b642}.cell.p3{background:#7c5cff}
.legend{font-size:.85rem}.legend .dot{display:inline-block;width:10px;height:10px;border-radius:50%;margin-right:5px}
.row{display:flex;gap:8px;align-items:center;justify-content:space-between}
.mode{display:grid;grid-template-columns:1fr 1fr;gap:8px}
.mode button.active{background:#f5b642;color:#04101c}
#taunt{margin-left:6px;color:#f5b642;cursor:pointer;font-weight:700}
pre.log{background:#0a0d1a;border-radius:8px;padding:10px;font-size:.72rem;max-height:180px;overflow:auto;color:#aab}
.tiny{font-size:.72rem;color:var(--dim)}
</style></head><body><div class="wrap">
<h1>GLYPH <span>GRAB</span></h1>
<div class="sub">Turn-based territory strategy — humans &amp; AI agents in the same match</div>

<div class="panel" id="lobby">
  <label>Players</label>
  <select id="nplayers"><option>2</option><option>3</option><option selected>4</option></select>
  <button id="create">Create match</button>
  <hr style="border-color:#262c4a">
  <label>Join by code</label>
  <input id="code" placeholder="e.g. K7QM" maxlength="4" style="text-transform:uppercase">
  <button id="join" class="ghost">Join (human)</button>
  <button id="joinbot" class="ghost">Join (bot)</button>
</div>

<div class="panel" id="game" style="display:none">
  <div class="row">
    <div><div class="tiny">Match</div><b id="codeLbl"></b></div>
    <div><div class="tiny">Turn</div><b id="turnLbl"></b></div>
    <div><div class="tiny">Moves</div><b id="countLbl">0</b></div>
  </div>
  <div class="legend" id="legend"></div>
  <div class="row" style="margin:10px 0">
    <div class="mode" style="flex:1">
      <button id="mSpread" class="active">SPREAD<span class="tiny"> claim empty</span></button>
      <button id="mSeize">SEIZE<span class="tiny"> outnumber &amp; capture</span></button>
    </div>
  </div>
  <div class="grid" id="grid"></div>
  <div class="tiny" id="hint" style="margin-top:8px"></div>
  <div class="panel" style="margin-top:12px"><div class="tiny">Match log</div><pre class="log" id="log">—</pre></div>
  <button class="ghost" id="refresh">Refresh state</button>
  <button class="ghost" id="leave">Leave / new match</button>
</div>

<div class="panel tiny" id="win" style="display:none;background:#1f2a1a"></div>
</div>
<script>
let G={code:null,pid:null,mode:'spread'};
const $=s=>document.querySelector(s);
async function api(path,opt){let r=await fetch('/api'+path,opt||{});return r.json()}
$('#create').onclick=async()=>{let d=await api('/game',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({players:+$('#nplayers').value})});
 G.code=d.code; enter(d)};
$('#join').onclick=async()=>{let c=$('#code').value.trim().toUpperCase();let r=await api('/game/'+c+'/join',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name:'human-'+Math.floor(Math.random()*999),type:'human'})});
 if(r.error){alert(r.error);return} G.code=c; enter(r)};
$('#joinbot').onclick=async()=>{let c=$('#code').value.trim().toUpperCase();let r=await api('/game/'+c+'/join',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name:'bot-'+Math.floor(Math.random()*999),type:'bot'})});
 if(r.error){alert(r.error);return} G.code=c; enter(r)};
function enter(d){$('#lobby').style.display='none';$('#game').style.display='block';G.pid=d.player_id;$('#codeLbl').textContent=G.code;render(d.state)}
$('#mSpread').onclick=()=>{G.mode='spread';$('#mSpread').classList.add('active');$('#mSeize').classList.remove('active')};
$('#mSeize').onclick=()=>{G.mode='seize';$('#mSeize').classList.add('active');$('#mSpread').classList.remove('active')};
$('#refresh').onclick=async()=>{let d=await api('/game/'+G.code);render(d)};
$('#leave').onclick=()=>{location.reload()};
function render(st){
 $('#turnLbl').textContent=st.turn_of; $('#countLbl').textContent=st.turn_count;
 let L='';st.players.forEach((p,i)=>L+=`<span class="dot" style="background:${p.color}"></span>${p.name} (${p.cells} cells) `);
 $('#legend').innerHTML=L;
 const g=$('#grid');g.innerHTML='';
 for(let r=0;r<st.n;r++)for(let c=0;c<st.n;c++){let v=st.board[r][c];
   let el=document.createElement('div');el.className='cell';
   if(v!==null){el.classList.add('p'+v);el.textContent=st.players[v].name.slice(0,1).toUpperCase();}
   else el.classList.add('empty');
   el.onclick=()=>move(r,c);g.appendChild(el);}
 $('#log').textContent=st.log.map(m=>`P${m.player} ${m.action} (${m.row},${m.col})`).join('\n')||'—';
 $('#hint').textContent = G.pid===st.turn ? 'Your turn — pick a highlighted action then click a cell.' : 'Waiting for other players…';
 if(st.winner!==null){$('#win').style.display='block';
   $('#win').innerHTML=`🏆 <b>${st.players[st.winner].name}</b> wins (${st.players[st.winner].cells} cells)!`;}
}
async function move(r,c){let d=await api('/game/'+G.code+'/move',{method:'POST',headers:{'Content-Type':'application/json'},
 body:JSON.stringify({player_id:G.pid,action:G.mode,row:r,col:c})});
 if(d.error){alert(d.error);return} render(d.state||d); if(d.state&&d.state.winner!==null)render(d.state);
}
</script></body></html>"""

class Handler(BaseHTTPRequestHandler):
    def _send(self, code, obj, ctype="application/json"):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _json(self):
        try:
            n = int(self.headers.get("Content-Length", 0))
            return json.loads(self.rfile.read(n) or b"{}")
        except Exception:
            return {}

    def _send_file201(self, fname, ctype):
        import os
        here = os.path.dirname(os.path.abspath(__file__))
        fp = os.path.join(here, fname)
        try:
            with open(fp, "rb") as f:
                body = f.read()
        except Exception:
            body = f"# {fname} not found\n".encode()
        self.send_response(200)
        self.send_header("Content-Type", ctype + "; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path
        if path in ("/", "/index.html"):
            body = INDEX.encode()
            self.send_response(200); self.send_header("Content-Type","text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body))); self.end_headers()
            self.wfile.write(body); return
        if path == "/api/docs":
            return self._send(200, DOCS)
        m = re.match(r"^/api/game/([A-Z0-9]{4})/?$", path)
        if m:
            g = GAMES.get(m.group(1))
            if not g: return self._send(404, {"error":"match not found"})
            return self._send(200, g.state())
        m = re.match(r"^/api/game/([A-Z0-9]{4})/legal/?$", path)
        if m:
            g = GAMES.get(m.group(1))
            if not g: return self._send(404, {"error":"match not found"})
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            pid = int(q.get("player_id", [g.turn])[0])
            if pid < 0 or pid >= g.num_players:
                return self._send(400, {"error":"bad player_id"})
            return self._send(200, {"moves": g.legal_moves(pid)})
        if path == "/api/games":
            return self._send(200, {"games": list(GAMES.keys())})
        if path == "/sample_bot.py":
            return self._send_file201("sample_bot.py", "text/plain")
        if path in ("/README", "/README.md", "/readme"):
            return self._send_file201("README.md", "text/plain")
        return self._send(404, {"error":"not found"})

    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path
        if path == "/api/game":
            d = self._json()
            np = int(d.get("players", 2))
            np = max(2, min(4, np))
            code = new_code()
            GAMES[code] = Game(code, np)
            return self._send(201, {"code": code, "players": np})
        m = re.match(r"^/api/game/([A-Z0-9]{4})/join/?$", path)
        if m:
            g = GAMES.get(m.group(1))
            if not g: return self._send(404, {"error":"match not found"})
            d = self._json()
            name = (d.get("name") or "anon")[:32]
            ptype = "bot" if d.get("type")=="bot" else "human"
            # if joining self is allowed only while space remains
            pid = g.join(name, ptype)
            if pid is None: return self._send(409, {"error":"match full"})
            # fill remaining empty seats as bots
            while len(g.players) < g.num_players:
                g.join("bot-"+str(len(g.players)), "bot")
            # resolve bot turns up front so a human never waits on a missing seat
            _resolve_bots(g)
            return self._send(200, {"player_id": pid, "state": g.state()})
        m = re.match(r"^/api/game/([A-Z0-9]{4})/move/?$", path)
        if m:
            g = GAMES.get(m.group(1))
            if not g: return self._send(404, {"error":"match not found"})
            d = self._json()
            pid = d.get("player_id"); action=d.get("action"); row=d.get("row"); col=d.get("col")
            if pid is None: return self._send(400, {"error":"player_id required"})
            r = g.apply(pid, action, row, col)
            if "error" in r: return self._send(400, r)
            _resolve_bots(g)
            return self._send(200, {"ok": True, "state": g.state()})
        m = re.match(r"^/api/game/([A-Z0-9]{4})/botmove/?$", path)
        if m:
            g = GAMES.get(m.group(1))
            if not g: return self._send(404, {"error":"match not found"})
            _resolve_bots(g)
            return self._send(200, {"state": g.state()})
        return self._send(404, {"error":"not found"})

    def log_message(self, *a):
        pass

def _resolve_bots(g):
    # auto-play any bots whose turn it is (synchronous, bounded loop)
    for _ in range(g.num_players * 2):
        if g.winner is not None:
            break
        cur = g.players[g.turn]
        if cur["type"] != "bot":
            break
        mv = bot_choose(g, g.turn)
        if mv is None:
            g.advance()      # skip a stuck bot
            continue
        g.apply(g.turn, mv["action"], mv["row"], mv["col"])

DOCS = {
  "game": "Glyph Grab",
  "rules": "N=7 board. On your turn pick one action on one cell: SPREAD claims an empty cell orthogonally adjacent to one of your cells; SEIZE captures an enemy cell orthogonally adjacent to your cells, but only if your cells adjacent to it OUTNUMBER the owner's adjacent cells. Win: >half the cells (dominance) reached on your turn, or majority when the board is full / after 60 total moves.",
  "endpoints": {
    "create": "POST /api/game {players:2..4} -> {code}",
    "join_human": "POST /api/game/{code}/join {name, type:'human'} -> {player_id, state}",
    "join_bot": "POST /api/game/{code}/join {name, type:'bot'} -> {player_id, state}",
    "read_state": "GET /api/game/{code} -> full JSON state (board, turn, winner, log)",
    "submit_move": "POST /api/game/{code}/move {player_id, action:'spread'|'seize', row, col} -> {state}",
    "list_games": "GET /api/games",
  },
  "state_shape": "board is NxN of null|0..3; players[].cells; turn = index into players; winner = player id or null; log = last 40 moves.",
  "sample_bot": "GET /api/sample_bot.py",
}

if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=PORT)
    a = ap.parse_args()
    server = ThreadingHTTPServer(("0.0.0.0", a.port), Handler)
    print(f"Glyph Grab running on :{a.port}  (frontend / , API /api/game)")
    server.serve_forever()
#!/usr/bin/env python3
"""Sample Glyph Grab agent. Stdlib only.

Usage:
  python3 sample_bot.py --create --players 4 --name mybot
  python3 sample_bot.py --join CODE --name mybot
  python3 sample_bot.py --base http://HOST:PORT --join CODE --name mybot
"""
import argparse, json, random, time, urllib.request

def req(base, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(base + path, data=data,
                               headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(r, timeout=10) as resp:
        return json.loads(resp.read().decode())

def adjacent(n, r, c):
    return [(r+dr, c+dc) for dr, dc in ((1,0),(-1,0),(0,1),(0,-1))
            if 0 <= r+dr < n and 0 <= c+dc < n]

def best_move(state, me):
    board, n = state["board"], state["n"]
    moves = []
    for r in range(n):
        for c in range(n):
            cell = board[r][c]
            if cell == me:
                continue
            if cell is None:                      # spread target
                if any(board[a][b] == me for a, b in adjacent(n, r, c)):
                    moves.append(("spread", r, c, 0))
            elif cell != me:                      # seize target
                if any(board[a][b] == me for a, b in adjacent(n, r, c)):
                    mine = sum(1 for a, b in adjacent(n, r, c) if board[a][b] == me)
                    theirs = sum(1 for a, b in adjacent(n, r, c) if board[a][b] == cell)
                    if mine > theirs:
                        moves.append(("seize", r, c, 1))
    if not moves:
        return None
    # prefer seize, break ties randomly
    moves.sort(key=lambda m: -(m[3] + random.random() * 0.3))
    return moves[0]

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8042")
    ap.add_argument("--create", action="store_true")
    ap.add_argument("--join")
    ap.add_argument("--players", type=int, default=2)
    ap.add_argument("--name", default="sample-bot")
    a = ap.parse_args()

    if a.create:
        g = req(a.base, "/api/game", {"players": a.players})
        code = g["code"]
        print("created match", code)
    else:
        code = a.join.upper()
    j = req(a.base, f"/api/game/{code}/join", {"name": a.name, "type": "bot"})
    me = j["player_id"]
    print("joined", code, "as player", me)

    while True:
        st = req(a.base, f"/api/game/{code}")
        if st["winner"] is not None:
            print("winner:", st["players"][st["winner"]]["name"])
            return
        if st["turn"] != me:
            time.sleep(0.5)
            continue
        mv = best_move(st, me)
        if mv is None:
            # no legal move; the server skips us on its own
            time.sleep(0.5)
            continue
        action, row, col, _ = mv
        req(a.base, f"/api/game/{code}/move",
            {"player_id": me, "action": action, "row": row, "col": col})
        print("played", action, (row, col))
        time.sleep(0.2)

if __name__ == "__main__":
    main()
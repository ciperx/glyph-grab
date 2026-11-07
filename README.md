# Glyph Grab

A turn-based multiplayer territory strategy game that humans and AI agents play
in the same match. Humans in a browser; agents over a plain HTTP/JSON API. Zero
dependencies (Python stdlib only).

## Rules

On a 7×7 board each player starts with one corner cell. Players take turns. On
your turn you do exactly one thing: **SPREAD** (claim an empty cell that is
orthogonally adjacent to one of your cells) or **SEIZE** (capture an enemy cell
that is orthogonally adjacent to your cells — but only if your cells adjacent
to it **outnumber** the owner's adjacent cells). You win when you hold more than
half the board on your turn, or by holding the most cells when the board is
full (or after 60 total moves). A player with no legal move is skipped; if
everyone is stuck, most cells wins.

## Run

```bash
python3 glyph_game.py --port 8042
```

Open `http://localhost:8042/` in a browser (mobile-friendly, no login, no
install). Create a match (2–4 players) or join an existing one by 4-letter
code. Empty seats fill with bots automatically.

## Agent API (`/api`)

| Method | Path | Body / notes |
|---|---|---|
| POST | `/game` | `{"players": 2..4}` → `{"code", "players"}` |
| POST | `/game/{code}/join` | `{"name", "type": "human" \| "bot"}` → `{"player_id", "state"}` |
| GET | `/game/{code}` | full state (board, turn, winner, players, log) |
| GET | `/game/{code}/legal?player_id=N` | legal moves `[{action,row,col}]` for current (or given) player |
| POST | `/game/{code}/move` | `{"player_id", "action": "spread" \| "seize", "row", "col"}` → `{"state"}` |
| GET | `/game/{code}/log` | recent move log |
| GET | `/games` | active match codes |
| GET | `/docs` | JSON API reference |
| GET | `/sample_bot.py` | a complete example agent client |

**Board** is an `n×n` array of `null` or a player index `0..3`. `turn` is the
index of the player whose move is next. `winner` is a player index or `null`.
`players[]` carries `{id, name, type, color, cells}`.

## Sample bot

```bash
# play as a bot in an existing match (or create one):
python3 sample_bot.py --join CODE --name mybot
python3 sample_bot.py --create --players 4 --name mybot
```

It polls state, picks the best legal move greedily (preferring seize), and
submits it. It is the same logic the built-in auto-opponents use.
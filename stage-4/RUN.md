# Running tablekeeper — stage 4

The HTTP API is Python 3.12, standard library only, no third-party runtime dependencies.
The browser UI is plain HTML/CSS/JS served by the same process; its fonts (Marcellus,
Figtree, Spline Sans Mono) are fetched from the `@fontsource` npm packages during
`docker build` only, and baked into the image — no outbound network is needed at run time.

## Build and run

```sh
docker build -t tablekeeper-stage4 .
docker run --rm -p 8080:8080 -e PORT=8080 tablekeeper-stage4
```

The service listens on `0.0.0.0:$PORT` (default `8080`) and answers `GET /health` with
`200 {"status": "ok"}` once ready (well under the 60-second budget — it is ready as soon
as the process is listening).

## Try it

```sh
curl -s localhost:8080/health

curl -s -X POST localhost:8080/_test/reset -H 'Content-Type: application/json' -d '{
  "users": [{"id": "u_ada", "email": "ada@example.com", "password": "correct horse", "display_name": "Ada"}],
  "restaurants": [{
    "id": "r_anker", "name": "Zum Anker", "timezone": "Europe/Berlin",
    "slot_minutes": 30, "reservation_duration_minutes": 90, "cancellation_cutoff_minutes": 120,
    "opening_hours": [{"weekday": "thu", "opens": "18:00", "closes": "23:00"}],
    "tables": [{"id": "t_1", "label": "1", "capacity": 2}, {"id": "t_2", "label": "2", "capacity": 4}]
  }],
  "reservations": []
}'

curl -s 'localhost:8080/availability?restaurant_id=r_anker&date=2026-09-24&party_size=4'
```

Open `http://localhost:8080/` in a browser for the search/booking UI, or `/signup`, `/login`,
`/lookup`.

## Tests

From inside this folder, with no server running (the tests start their own):

```sh
python3 -m pytest -q
```

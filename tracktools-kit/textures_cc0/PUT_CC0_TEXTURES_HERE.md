# Drop CC0 PBR textures here (commercial-clean)

The Blender driver (`automation/build_track_blender.py`) looks for these filenames:

- `asphalt_albedo.jpg`  (road base color)
- `asphalt_normal.jpg`  (road normal, optional)
- `grass_albedo.jpg`    (terrain/runoff base color)

Get them from **CC0** libraries (no attribution required, fully commercial):
- ambientCG — https://ambientcg.com  (`Asphalt###`, `Ground###`/grass)
- Poly Haven — https://polyhaven.com/textures  (`asphalt`, `aerial_grass`)

If a file is missing, the driver falls back to a solid color — so the pipeline
still runs, just untextured. Do NOT copy the repo's existing
`public/data/textures/*` here unless their license is confirmed CC0.

# Micro-Manager workshop at I2KxBINA2026

This repository will contain instructions and materials for the workshop titled *Micro-Manager from GUI to Python: Interactive Control, Configuration, and Scripted Data-Driven Acquisition* at [I2KxBINA2026](https://www.bioimagingnorthamerica.org/events/i2kxbina2026/), by Mark Tsuchida and Gabriel Selzer.

## Pre-workshop instructions (software installation)

If you'd like to try out Micro-Manager as you follow along, please install it according to our specific instructions [here](install-mm.md). This includes instructions for installing the SimulatedMicroscope device which is not part of the official Micro-Manager installer yet.

For the Python scripting section, you'll need `uv` to try the hands-on examples. Follow the official install instructions [here](https://docs.astral.sh/uv/#installation). You're good to go if the following command prints "uv works" in green:

```sh
uv run --with rich python -c "import rich; rich.print('[bold green]uv works[/bold green]')"
```

(Thank you for your patience if you've checked here earlier and were awaiting these instructions.)

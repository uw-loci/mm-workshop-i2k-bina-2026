# Data-Driven Acquisition with `pymmcore-plus`

**Data-Driven Acquisition** describes the process of acquiring data, analyzing that data in real time, and using the results of that analysis to inform the remainder of the acquisition. Data-driven acquisition offers the ability to discover and observe phenomena of interest faster and with less phototoxicity than exhaustive imaging. This folder describes how to get started performing data-driven acquisition with the [`pymmcore-plus`](https://pymmcore-plus.github.io/pymmcore-plus/) project.

## Use Case

This code utilizes a *simulated* microscope, imaging *"nuclei"* sparsely populated on a sample slide. These nuclei contain *puncta*, which are only clearly visible using a high-resolution objective.

Our goal is to obtain high-resolution images of the nuclei (so that we could hypothetically analyze the puncta), but because the nuclei are *sparse*, high-resolution imaging of the entire slide would be *wasteful*, as most of the images would be devoid of nuclei and their puncta. Data-driven acquisition will allow us to *avoid this waste* by only performing high-resolution imaging *at the nuclei*.

## Process

1. **Low-resolution grid scan** — a serpentine tile scan is performed using
   a 10× objective. Each tile is written to `data/low_res.ome.zarr` and painted
   into the live slide viewer as it arrives.

2. **On-the-fly nucleus detection** — [StarDist2D](https://github.com/stardist/stardist)
   runs on each tile immediately after capture. The resulting label image is
   overlaid on the slide viewer, and each label region is processed into a nucleus centroid.

3. **High-resolution follow-up** — each detected nucleus centroid is converted
   to stage coordinates and a 100× snapshot is queued. Follow-up images
   accumulate in `data/high_res.ome.zarr` and in a second live viewer.

## Run it!

To run this script, first ensure that [uv](https://docs.astral.sh/uv/) is installed on your system.

Second, ensure that [Micro-Manager](https://micro-manager.org/) is installed on your system.

> [!TIP]
> The easiest way to install Micro-Manager is via `pymmcore-plus`:
> ```bash
> uv venv
> uv pip install pymmcore-plus
> uv run mmcore install
> ```

Then, use `uv` to run the acquisition!

```bash
uv run data_driven_acq.py
```

> [!NOTE]
> This script has only been tested on Windows. The simulated camera adapter (`mmgr_dal_SimulatedCamera.dll`) is a Windows binary, so the script is unlikely to work on macOS or Linux without a platform-appropriate adapter.


## Architecture

Data acquisitions in `pymmcore-plus` are run through an [`MDARunner`](https://pymmcore-plus.github.io/pymmcore-plus/api/mda/), which takes a series of [`MDAEvent`](https://pymmcore-plus.github.io/useq-schema/schema/event/#useq.MDAEvent)s, each describing a single image from the camera.

While `pymmcore-plus` provides many pre-built `MDASequence`s, here we define our own sequence -  `DataDrivenMDA` in [`data_driven_mda.py`](data_driven_mda.py) - that separates a data-driven acquisition into
three overridable hooks:

| Method | Role |
|--------|------|
| `steady_state()` | Yield the baseline sequence of acquisition events |
| `find_events(img, event)` | Analyse a frame and yield events (objects, processes, etc.) of interest |
| `actuate_event(item)` | Yield one or more `MDAEvent`s in response to a detected event |

`NucleiFinder` in [`data_driven_acq.py`](data_driven_acq.py) implements this pattern concretely for our particular use case.

## Files

| File | Contents |
|------|----------|
| [`data_driven_mda.py`](data_driven_mda.py) | `DataDrivenMDA` — reusable base class |
| [`data_driven_acq.py`](data_driven_acq.py) | `NucleiFinder`, `Centroid`, `initialize_core`, `main` |
| [`_writers.py`](_writers.py) | `ScanWriter`, `POIWriter` — stream tiles/POIs to OME-Zarr |
| [`_viewers.py`](_viewers.py) | `ScanViewer`, `PoiViewer` — live NDV viewers |
| [`SimCamera.cfg`](SimCamera.cfg) | Micro-Manager config for the simulated camera/stage |

## Output

Both outputs are written to the `data/` subdirectory:

- `data/low_res.ome.zarr` — full tile scan (one position per tile)
- `data/high_res.ome.zarr` — high-resolution POI snapshots (one position per nucleus)

use std::{
    error::Error,
    fs::{self, File},
    io::{BufWriter, Write},
    ops::ControlFlow,
    path::PathBuf,
    time::Instant,
};

use clap::Parser;
use image::{DynamicImage, ImageDecoder, ImageReader, Limits};
use stringy::{Settings, generate_from_winding, render_preview};

#[derive(Parser)]
#[command(
    version,
    about = "Convert a photo into a single continuous black thread path"
)]
struct Args {
    /// JPEG, PNG, or WebP photo.
    #[arg(required_unless_present = "replay", conflicts_with = "replay")]
    input: Option<PathBuf>,
    /// Render a saved path.json to preview.png using the archived thread and anchors.
    #[arg(long)]
    replay: Option<PathBuf>,
    /// JSON array of initial nail IDs in the specified layout, for 2-opt refinement.
    #[arg(long, conflicts_with = "replay")]
    initial_winding: Option<PathBuf>,
    /// Optional RGB guidance PNG aligned with the final nail-field raster.
    #[arg(long)]
    guide: Option<PathBuf>,
    /// Replay the final path at 2048–4096 pixels and export a downsampled reference preview.
    #[arg(long, value_parser = clap::value_parser!(u32).range(64..=2048))]
    preview_size: Option<u32>,
    /// Add darkness from this aligned grayscale mask after applying the RGB guide.
    #[arg(long, requires = "guide")]
    background_ink: Option<PathBuf>,
    /// Directory for the two PNG previews, path.json, and segments.csv.
    #[arg(short, long, default_value = "output")]
    output: PathBuf,
    /// JSON array of [x,y] nail positions relative to the radius, centered, positive y down.
    #[arg(long, conflicts_with_all = ["anchors", "min_anchor_gap", "replay"])]
    nail_positions: Option<PathBuf>,
    /// Number of leading original ring nails whose circular skip rule is retained.
    #[arg(long, requires = "nail_positions")]
    base_ring: Option<usize>,
    /// Number of equally spaced anchors.
    #[arg(long, default_value_t = Settings::default().anchors)]
    anchors: usize,
    #[arg(long, default_value_t = Settings::default().diameter_mm)]
    diameter_mm: f64,
    /// Rectangular nail field width and height in mm, measured between outer nail centers.
    #[arg(long, num_args = 2, value_names = ["WIDTH", "HEIGHT"], conflicts_with = "diameter_mm")]
    rectangle_mm: Option<Vec<f64>>,
    /// Nail shaft diameter in mm. Rectangles default to 1.2 mm plus 0.5 mm clearance.
    #[arg(long)]
    shaft_mm: Option<f64>,
    #[arg(long, default_value_t = Settings::default().thread_mm)]
    thread_mm: f64,
    /// Magnification of the image crop.
    #[arg(long, default_value_t = Settings::default().zoom)]
    zoom: f64,
    /// Allow the selected crop to extend onto white outside the image.
    #[arg(long)]
    pad_crop: bool,
    /// Crop center as a percentage of the oriented image width.
    #[arg(long, default_value_t = Settings::default().center_x)]
    center_x: f64,
    /// Crop center as a percentage of the oriented image height.
    #[arg(long, default_value_t = Settings::default().center_y)]
    center_y: f64,
    /// Brightness exponent for the target. Values below one lift midtones.
    #[arg(long, default_value_t = Settings::default().gamma)]
    gamma: f32,
    /// Maximum path-refinement sweeps (0 to 12).
    #[arg(long, default_value_t = Settings::default().passes)]
    passes: usize,
    /// Priority given to local contrast in the photograph (0 to 20).
    #[arg(long, default_value_t = Settings::default().detail)]
    detail: f32,
    /// Thread opacity at full pixel coverage.
    #[arg(long, default_value_t = Settings::default().opacity)]
    opacity: f32,
    #[arg(long, default_value_t = Settings::default().max_segments)]
    max_segments: usize,
    /// Width and height of the optimization raster in pixels.
    #[arg(long, default_value_t = Settings::default().raster_size)]
    raster_size: usize,
    /// Minimum circular distance in anchors (default: 8, reduced for small anchor counts).
    #[arg(long)]
    min_anchor_gap: Option<usize>,
    /// Minimum straight-line distance between connected nails, in mm.
    #[arg(long, default_value_t = Settings::default().min_segment_mm)]
    min_segment_mm: f64,
    /// Maximum incident thread ends per nail, or zero for unlimited.
    #[arg(long, default_value_t = Settings::default().max_nail_load)]
    max_nail_load: usize,
    /// Maximum uses of an undirected chord, or zero for unlimited.
    #[arg(long, default_value_t = Settings::default().max_chord_uses)]
    max_chord_uses: u32,
    /// Additional weight assigned to near-white tones.
    #[arg(long, default_value_t = Settings::default().white_protection)]
    white_protection: f32,
    /// Starting nail ID. Automatic plans start at the top of the circle or top left of the rectangle.
    #[arg(long, default_value_t = Settings::default().start_anchor)]
    start_anchor: usize,
    /// Deduction in squared-error gain per earlier use of the same chord.
    #[arg(long, default_value_t = Settings::default().repeat_penalty)]
    repeat_penalty: f64,
    /// Evaluate alternative two-chord continuations at ambiguous decisions.
    #[arg(long)]
    lookahead: bool,
    /// Memory budget for cached raster samples. Use 0 for on-demand rasterization.
    #[arg(long, default_value_t = Settings::default().cache_mib)]
    cache_mib: usize,
}

fn main() -> Result<(), Box<dyn Error>> {
    let args = Args::parse();
    if let Some(path) = args.replay {
        #[derive(serde::Deserialize)]
        struct Winding {
            settings: Settings,
            path: Vec<usize>,
        }
        let saved: Winding = serde_json::from_reader(File::open(path)?)?;
        let preview = render_preview(
            &saved.settings,
            &saved.path,
            args.preview_size.unwrap_or(1024),
        )?;
        fs::create_dir_all(&args.output)?;
        preview.save(args.output.join("preview.png"))?;
        return Ok(());
    }
    let nail_positions: Vec<[f64; 2]> = match args.nail_positions {
        Some(path) => {
            let pins: Vec<[f64; 2]> = serde_json::from_reader(File::open(path)?)?;
            if pins.is_empty() {
                return Err("empty nail layout".into());
            }
            pins
        }
        None => Vec::new(),
    };
    let settings = Settings {
        anchors: if nail_positions.is_empty() {
            args.anchors
        } else {
            nail_positions.len()
        },
        min_anchor_gap: if nail_positions.is_empty() {
            args.min_anchor_gap.unwrap_or_else(|| {
                Settings::default()
                    .min_anchor_gap
                    .min((args.anchors / 4).max(1))
            })
        } else {
            args.base_ring.map_or(1, |n| {
                Settings::default().min_anchor_gap.min((n / 4).max(1))
            })
        },
        base_ring: args.base_ring.unwrap_or(0),
        min_segment_mm: args.min_segment_mm,
        max_nail_load: args.max_nail_load,
        max_chord_uses: args.max_chord_uses,
        white_protection: args.white_protection,
        nail_positions,
        diameter_mm: args.diameter_mm,
        shaft_mm: args.shaft_mm.or(args.rectangle_mm.as_ref().map(|_| 1.2)),
        rectangle_mm: args.rectangle_mm.map(|d| [d[0], d[1]]),
        thread_mm: args.thread_mm,
        opacity: args.opacity,
        detail: args.detail,
        passes: args.passes,
        zoom: args.zoom,
        pad_crop: args.pad_crop,
        center_x: args.center_x,
        center_y: args.center_y,
        gamma: args.gamma,
        max_segments: args.max_segments,
        raster_size: args.raster_size,
        start_anchor: args.start_anchor,
        repeat_penalty: args.repeat_penalty,
        lookahead: args.lookahead,
        cache_mib: args.cache_mib,
    };
    let mut reader =
        ImageReader::open(args.input.expect("photo or replay required"))?.with_guessed_format()?;
    let mut limits = Limits::default();
    limits.max_image_width = Some(8192);
    limits.max_image_height = Some(8192);
    reader.limits(limits);
    let mut decoder = reader.into_decoder()?;
    let orientation = decoder.orientation()?;
    let mut source = DynamicImage::from_decoder(decoder)?;
    source.apply_orientation(orientation);

    let start = Instant::now();
    eprintln!(
        "Preparing the {} × {} canvas and line cache",
        settings.raster_size, settings.raster_size
    );
    let guide = args
        .guide
        .map(image::open)
        .transpose()?
        .map(|im| im.to_rgb8());
    let ink = args
        .background_ink
        .map(image::open)
        .transpose()?
        .map(|im| im.into_luma8());
    let initial: Option<Vec<usize>> = args.initial_winding.map(|p| -> Result<_, Box<dyn Error>> {
        Ok(serde_json::from_reader(File::open(p)?)?)
    }).transpose()?;
    let art = generate_from_winding(
        &source,
        &settings,
        guide.as_ref(),
        ink.as_ref(),
        initial.as_deref(),
        |progress| {
            if progress.segments > 0 && progress.segments % 1000 == 0 {
                eprintln!("{} segments", progress.segments);
            }
            ControlFlow::Continue(())
        },
    )?;
    let elapsed = start.elapsed();

    fs::create_dir_all(&args.output)?;
    art.target.save(args.output.join("target.png"))?;
    let preview = match args.preview_size {
        Some(size) => render_preview(&settings, &art.route.path, size)?,
        None => art.preview,
    };
    preview.save(args.output.join("preview.png"))?;
    let mut json = BufWriter::new(File::create(args.output.join("path.json"))?);
    serde_json::to_writer_pretty(&mut json, &art.route)?;
    writeln!(json)?;
    json.flush()?;
    let mut csv = BufWriter::new(File::create(args.output.join("segments.csv"))?);
    writeln!(csv, "step,from_anchor,to_anchor,length_mm")?;
    for (index, segment) in art.route.segments.iter().enumerate() {
        writeln!(
            csv,
            "{},{},{},{:.6}",
            index + 1,
            segment.from,
            segment.to,
            segment.length_mm
        )?;
    }
    csv.flush()?;

    println!(
        "{} segments, {:.2} m of thread, RMSE {:.4} → {:.4}, {:.2} s, {:.1} MiB cached ({:?})",
        art.route.segments.len(),
        art.route.total_length_m,
        art.route.initial_rmse,
        art.route.final_rmse,
        elapsed.as_secs_f64(),
        art.cache_bytes_used as f64 / (1024.0 * 1024.0),
        art.route.stop_reason,
    );
    Ok(())
}

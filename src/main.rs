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
use stringy::{Settings, generate};

#[derive(Parser)]
#[command(
    version,
    about = "Convert a photo into a single continuous black thread path"
)]
struct Args {
    /// JPEG, PNG, or WebP photo.
    input: PathBuf,
    /// Directory for the two PNG previews, path.json, and segments.csv.
    #[arg(short, long, default_value = "output")]
    output: PathBuf,
    /// Number of equally spaced anchors.
    #[arg(long, default_value_t = Settings::default().anchors)]
    anchors: usize,
    #[arg(long, default_value_t = Settings::default().diameter_mm)]
    diameter_mm: f64,
    #[arg(long, default_value_t = Settings::default().thread_mm)]
    thread_mm: f64,
    /// Magnification of the square crop.
    #[arg(long, default_value_t = Settings::default().zoom)]
    zoom: f64,
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
    /// First anchor, with 0 at the top of the canvas.
    #[arg(long, default_value_t = Settings::default().start_anchor)]
    start_anchor: usize,
    /// Deduction in squared-error gain per earlier use of the same chord.
    #[arg(long, default_value_t = Settings::default().repeat_penalty)]
    repeat_penalty: f64,
    /// Memory budget for cached raster samples. Use 0 for on-demand rasterization.
    #[arg(long, default_value_t = Settings::default().cache_mib)]
    cache_mib: usize,
}

fn main() -> Result<(), Box<dyn Error>> {
    let args = Args::parse();
    let settings = Settings {
        anchors: args.anchors,
        diameter_mm: args.diameter_mm,
        thread_mm: args.thread_mm,
        opacity: args.opacity,
        detail: args.detail,
        passes: args.passes,
        zoom: args.zoom,
        center_x: args.center_x,
        center_y: args.center_y,
        gamma: args.gamma,
        max_segments: args.max_segments,
        raster_size: args.raster_size,
        min_anchor_gap: args.min_anchor_gap.unwrap_or_else(|| {
            Settings::default()
                .min_anchor_gap
                .min((args.anchors / 4).max(1))
        }),
        start_anchor: args.start_anchor,
        repeat_penalty: args.repeat_penalty,
        cache_mib: args.cache_mib,
    };
    let mut reader = ImageReader::open(args.input)?.with_guessed_format()?;
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
    let art = generate(&source, &settings, |progress| {
        if progress.segments > 0 && progress.segments % 1000 == 0 {
            eprintln!("{} segments", progress.segments);
        }
        ControlFlow::Continue(())
    })?;
    let elapsed = start.elapsed();

    fs::create_dir_all(&args.output)?;
    art.target.save(args.output.join("target.png"))?;
    art.preview.save(args.output.join("preview.png"))?;
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

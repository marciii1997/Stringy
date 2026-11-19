mod exchange;
mod objective;
mod preview;
mod raster;
use objective::Objective;
pub use preview::render_preview;

use std::{
    fmt,
    mem::size_of,
    ops::{ControlFlow, Range},
};

use image::{DynamicImage, GrayImage};
use raster::{Canvas, Sample};
use rayon::prelude::*;
use serde::{Deserialize, Serialize};

pub use raster::Anchor;

const MIB: usize = 1024 * 1024;
const MIN_GAIN: f64 = 1e-8;

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(default)]
pub struct Settings {
    pub anchors: usize,
    /// Coordinates relative to the canvas radius, positive y down. Empty selects the ring.
    #[serde(skip_serializing_if = "Vec::is_empty")]
    pub nail_positions: Vec<[f64; 2]>,
    pub diameter_mm: f64,
    pub thread_mm: f64,
    pub opacity: f32,
    pub detail: f32,
    pub passes: usize,
    pub zoom: f64,
    pub pad_crop: bool,
    pub center_x: f64,
    pub center_y: f64,
    pub gamma: f32,
    pub max_segments: usize,
    pub raster_size: usize,
    pub min_anchor_gap: usize,
    pub start_anchor: usize,
    pub repeat_penalty: f64,
    pub cache_mib: usize,
}

impl Default for Settings {
    fn default() -> Self {
        Self {
            anchors: 360,
            nail_positions: Vec::new(),
            diameter_mm: 600.0,
            thread_mm: 0.25,
            opacity: 1.0,
            detail: 1.0,
            passes: 3,
            zoom: 1.0,
            pad_crop: false,
            center_x: 50.0,
            center_y: 50.0,
            gamma: 1.0,
            max_segments: 12_000,
            raster_size: 512,
            min_anchor_gap: 8,
            start_anchor: 0,
            repeat_penalty: 0.0005,
            cache_mib: 384,
        }
    }
}

#[derive(Debug)]
pub struct InvalidInput(&'static str);

impl fmt::Display for InvalidInput {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(self.0)
    }
}

impl std::error::Error for InvalidInput {}

impl Settings {
    fn validate(&self) -> Result<(), InvalidInput> {
        if !(3..=720).contains(&self.anchors) {
            return Err(InvalidInput("anchors must be between 3 and 720"));
        }
        if !self.nail_positions.is_empty() {
            if self.nail_positions.len() != self.anchors {
                return Err(InvalidInput("anchor count disagrees with nail positions"));
            }
            for (i, &[x, y]) in self.nail_positions.iter().enumerate() {
                if !x.is_finite() || !y.is_finite() || x.hypot(y) > 1.0 + 1e-12 {
                    return Err(InvalidInput("nail outside canvas"));
                }
                if self.nail_positions[..i]
                    .iter()
                    .any(|p| (x - p[0]).hypot(y - p[1]) < 1e-6)
                {
                    return Err(InvalidInput("coincident nail positions"));
                }
            }
        }
        if !(64..=1024).contains(&self.raster_size) {
            return Err(InvalidInput(
                "raster size must be between 64 and 1024 pixels",
            ));
        }
        if !self.diameter_mm.is_finite() || !(1.0..=100_000.0).contains(&self.diameter_mm) {
            return Err(InvalidInput(
                "canvas diameter must be between 1 and 100000 mm",
            ));
        }
        if !self.thread_mm.is_finite()
            || !(0.001..=self.diameter_mm / 10.0).contains(&self.thread_mm)
        {
            return Err(InvalidInput(
                "thread diameter must be between 0.001 mm and one tenth of the canvas diameter",
            ));
        }
        if !(0.01..=5.0).contains(&self.zoom)
            || !(0.0..=100.0).contains(&self.center_x)
            || !(0.0..=100.0).contains(&self.center_y)
        {
            return Err(InvalidInput(
                "zoom must be 0.01–5 and crop center must be 0–100 percent",
            ));
        }
        if !(0.3..=3.0).contains(&self.gamma) {
            return Err(InvalidInput("gamma must be 0.3–3"));
        }
        if self.passes > 12 {
            return Err(InvalidInput("refinement passes must be at most 12"));
        }
        if !(0.0..=20.0).contains(&self.detail) {
            return Err(InvalidInput("detail must be between 0 and 20"));
        }
        if !(0.0 < self.opacity && self.opacity <= 1.0) {
            return Err(InvalidInput(
                "opacity must be greater than zero and at most one",
            ));
        }
        if self.nail_positions.is_empty() && !(1..=self.anchors / 2).contains(&self.min_anchor_gap)
        {
            return Err(InvalidInput(
                "minimum anchor gap must be between 1 and half the anchor count",
            ));
        }
        if self.start_anchor >= self.anchors {
            return Err(InvalidInput("start anchor is outside the anchor range"));
        }
        if self.max_segments > 100_000 {
            return Err(InvalidInput("maximum segment count must be at most 100000"));
        }
        if !self.repeat_penalty.is_finite() || self.repeat_penalty < 0.0 {
            return Err(InvalidInput(
                "repeat penalty must be finite and nonnegative",
            ));
        }
        if self.cache_mib > 1024 {
            return Err(InvalidInput("line cache budget must be at most 1024 MiB"));
        }
        Ok(())
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum StopReason {
    SegmentLimit,
    NoImprovingMove,
    Cancelled,
}

#[derive(Debug)]
pub struct Progress {
    pub segments: usize,
    pub anchor: usize,
}

#[derive(Debug, Serialize)]
pub struct Segment {
    pub from: usize,
    pub to: usize,
    pub length_mm: f64,
}

#[derive(Debug, Serialize)]
pub struct Route {
    pub format_version: u32,
    pub optimizer_version: u32,
    pub accepted_global_passes: usize,
    pub settings: Settings,
    pub anchors: Vec<Anchor>,
    pub path: Vec<usize>,
    pub segments: Vec<Segment>,
    pub total_length_m: f64,
    pub initial_rmse: f64,
    pub final_rmse: f64,
    pub stop_reason: StopReason,
}

pub struct Artwork {
    pub route: Route,
    pub target: GrayImage,
    pub preview: GrayImage,
    pub cache_bytes_used: usize,
}

#[derive(Clone, Copy)]
struct Move {
    anchor: usize,
    line: usize,
}

struct Line {
    endpoints: (usize, usize),
    length_mm: f64,
    samples: Range<usize>,
}

struct Lines {
    lines: Vec<Line>,
    moves: Vec<Vec<Move>>,
    cache: Vec<Sample>,
    width_px: f64,
    coarse_cells: Vec<[u32; 48]>,
    refinement_neighbors: Vec<Vec<usize>>,
}

// A direct winding step cannot pass through the center of an intervening nail.
// Coordinates use the unit radius; the tolerance only covers rounding at collinearity.
fn crosses_intermediate_nail(pins: &[[f64; 2]], from: usize, to: usize) -> bool {
    if pins.is_empty() {
        return false;
    }
    let [x, y] = pins[from];
    let dx = pins[to][0] - x;
    let dy = pins[to][1] - y;
    let length_sq = dx * dx + dy * dy;
    let tolerance = 1e-10 * length_sq.sqrt();
    pins.iter().enumerate().any(|(i, p)| {
        if i == from || i == to {
            return false;
        }
        let px = p[0] - x;
        let py = p[1] - y;
        let along = px * dx + py * dy;
        along > 0. && along < length_sq && (px * dy - py * dx).abs() <= tolerance
    })
}

impl Lines {
    fn new(canvas: &Canvas, settings: &Settings) -> Self {
        let width_px = settings.thread_mm / settings.diameter_mm * (canvas.size - 1) as f64;
        let sample_capacity = settings.cache_mib * MIB / size_of::<Sample>();
        let mut lines = Vec::new();
        let mut moves = vec![Vec::new(); settings.anchors];
        let mut cache = Vec::new();
        let mut scratch = Vec::new();
        let mut cache_has_room = sample_capacity > 0 && settings.max_segments > 0;

        let gap = if settings.nail_positions.is_empty() {
            settings.min_anchor_gap
        } else {
            1
        };
        for from in 0..settings.anchors {
            for to in from + gap..settings.anchors {
                if settings.anchors - (to - from) < gap
                    || crosses_intermediate_nail(&settings.nail_positions, from, to)
                {
                    continue;
                }
                let id = lines.len();
                moves[from].push(Move {
                    anchor: to,
                    line: id,
                });
                moves[to].push(Move {
                    anchor: from,
                    line: id,
                });
                let length_mm = (canvas.anchors[to].x_mm - canvas.anchors[from].x_mm)
                    .hypot(canvas.anchors[to].y_mm - canvas.anchors[from].y_mm);
                let start = cache.len();
                if cache_has_room {
                    scratch.clear();
                    canvas.rasterize(canvas.points[from], canvas.points[to], width_px, |s| {
                        scratch.push(s)
                    });
                    if scratch.len() <= sample_capacity - start {
                        let needed = start + scratch.len();
                        if needed > cache.capacity() {
                            let next = needed.max(cache.capacity() * 2).min(sample_capacity);
                            cache.reserve_exact(next - start);
                        }
                        cache.extend_from_slice(&scratch);
                    } else {
                        cache_has_room = false;
                    }
                }
                lines.push(Line {
                    endpoints: (from, to),
                    length_mm,
                    samples: start..cache.len(),
                });
            }
        }
        let coarse_cells = if canvas.size >= 768 && settings.anchors > 96 {
            lines
                .iter()
                .map(|line| {
                    let (a, b) = line.endpoints;
                    let (p, q) = (canvas.points[a], canvas.points[b]);
                    std::array::from_fn(|i| {
                        let t = (i as f64 + 0.5) / 48.0;
                        let x = (p.x + t * (q.x - p.x)).round() as usize;
                        let y = (p.y + t * (q.y - p.y)).round() as usize;
                        objective::cell_index(x, y, canvas.size) as u32
                    })
                })
                .collect()
        } else {
            Vec::new()
        };
        let refinement_neighbors = (0..settings.anchors)
            .map(|id| {
                if settings.nail_positions.is_empty() {
                    return [-16, -8, -4, -2, -1, 1, 2, 4, 8, 16]
                        .iter()
                        .map(|offset| {
                            (id as isize + offset).rem_euclid(settings.anchors as isize) as usize
                        })
                        .collect();
                }
                let mut neighbors = moves[id].clone();
                neighbors
                    .sort_by(|a, b| lines[a.line].length_mm.total_cmp(&lines[b.line].length_mm));
                neighbors.iter().take(10).map(|m| m.anchor).collect()
            })
            .collect();
        Self {
            refinement_neighbors,
            coarse_cells,
            lines,
            moves,
            cache,
            width_px,
        }
    }

    fn id(&self, from: usize, to: usize) -> Option<usize> {
        self.moves[from]
            .binary_search_by_key(&to, |m| m.anchor)
            .ok()
            .map(|i| self.moves[from][i].line)
    }

    fn visit(&self, canvas: &Canvas, id: usize, mut callback: impl FnMut(Sample)) {
        let line = &self.lines[id];
        if line.samples.is_empty() {
            let (from, to) = line.endpoints;
            canvas.rasterize(
                canvas.points[from],
                canvas.points[to],
                self.width_px,
                callback,
            );
        } else {
            for &sample in &self.cache[line.samples.clone()] {
                callback(sample);
            }
        }
    }
}

fn rmse(canvas: &Canvas, target: &[f32], current: &[f32]) -> f64 {
    let mut squared_error = 0.0;
    let mut pixels = 0;
    for ((&target, &current), &inside) in target.iter().zip(current).zip(&canvas.inside) {
        if inside {
            squared_error += f64::from(target - current).powi(2);
            pixels += 1;
        }
    }
    (squared_error / f64::from(pixels)).sqrt()
}

struct Search<'a> {
    canvas: &'a Canvas,
    lines: &'a Lines,
    target: &'a [f32],
    settings: &'a Settings,
    image: Objective,
    repeats: Vec<u32>,
    scratch: Vec<objective::Scratch>,
    exact_after_bridge: bool,
}

impl Search<'_> {
    fn change(&mut self, id: usize, add: bool) {
        self.image
            .change(self.canvas, self.lines, id, self.settings.opacity, add);
        if add {
            self.repeats[id] += 1;
        } else {
            self.repeats[id] -= 1;
        }
    }

    fn gain(&mut self, line: usize) -> f64 {
        self.image.gain(
            self.canvas,
            self.lines,
            line,
            self.target,
            self.settings.opacity,
            &mut self.scratch[0],
        ) - self.settings.repeat_penalty * f64::from(self.repeats[line])
    }

    fn pair_gain(&mut self, first: usize, second: usize) -> f64 {
        let one = self.gain(first);
        self.change(first, true);
        let two = self.gain(second);
        self.change(first, false);
        one + two
    }

    fn score_moves(&mut self, candidates: &mut [(f64, Move)]) {
        candidates
            .par_chunks_mut(32)
            .zip(self.scratch.par_iter_mut())
            .for_each(|(chunk, scratch)| {
                for (score, m) in chunk {
                    *score = self.image.gain(
                        self.canvas,
                        self.lines,
                        m.line,
                        self.target,
                        self.settings.opacity,
                        scratch,
                    ) - self.settings.repeat_penalty * f64::from(self.repeats[m.line]);
                }
            });
    }

    fn next(&mut self, from: usize, remaining: usize) -> Option<Move> {
        let mut candidates: Vec<_> = self.lines.moves[from].iter().map(|&m| (0.0, m)).collect();
        let by_score = |(sa, a): &(f64, Move), (sb, b): &(f64, Move)| {
            sb.total_cmp(sa).then_with(|| a.anchor.cmp(&b.anchor))
        };
        let mut exact = candidates.len();
        let require_exact = std::mem::take(&mut self.exact_after_bridge);
        if !require_exact && !self.lines.coarse_cells.is_empty() && exact > 64 {
            for (score, m) in &mut candidates {
                *score = self.image.coarse_score(&self.lines.coarse_cells[m.line])
                    * self.lines.lines[m.line].length_mm;
            }
            candidates.select_nth_unstable_by(64, by_score);
            exact = 64;
        }
        self.score_moves(&mut candidates[..exact]);
        candidates[..exact].sort_unstable_by(by_score);
        if candidates[0].0 > MIN_GAIN {
            return Some(candidates[0].1);
        }
        // A coarse shortlist alone must never terminate a route.
        if exact < candidates.len() {
            self.score_moves(&mut candidates[exact..]);
            candidates.sort_unstable_by(by_score);
        }
        if candidates[0].0 > MIN_GAIN {
            return Some(candidates[0].1);
        }
        if remaining < 2 {
            return None;
        }
        // Try low-cost connectors and a spread of more expensive ones. The next
        // greedy move can always take the continuation evaluated here.
        let mut best = MIN_GAIN;
        let mut bridge = None;
        for &(first_gain, first) in candidates.iter().take(8).chain(
            candidates
                .iter()
                .skip(8)
                .step_by((candidates.len() / 24).max(1)),
        ) {
            self.change(first.line, true);
            for second in &self.lines.moves[first.anchor] {
                let combined = first_gain + self.gain(second.line);
                if combined > best {
                    best = combined;
                    bridge = Some(first);
                }
            }
            self.change(first.line, false);
        }
        // An approximate shortlist could omit the continuation that paid for the connector.
        self.exact_after_bridge = bridge.is_some();
        bridge
    }

    // Reconsider earlier greedy decisions. Moving an interior vertex replaces
    // two chords and keeps the entire route windable from the same start.
    fn refine(&mut self, path: &mut [usize]) -> usize {
        let mut changed = 0;
        for i in 1..path.len().saturating_sub(1) {
            let (u, v, w) = (path[i - 1], path[i], path[i + 1]);
            let left = self.lines.id(u, v).unwrap();
            let right = self.lines.id(v, w).unwrap();
            self.change(left, false);
            self.change(right, false);
            let mut best = self.pair_gain(left, right);
            let mut winner = (v, left, right);
            for &x in &self.lines.refinement_neighbors[v] {
                if let (Some(a), Some(b)) = (self.lines.id(u, x), self.lines.id(x, w)) {
                    let gain = self.pair_gain(a, b);
                    if gain > best + MIN_GAIN {
                        best = gain;
                        winner = (x, a, b);
                    }
                }
            }
            path[i] = winner.0;
            self.change(winner.1, true);
            self.change(winner.2, true);
            changed += usize::from(v != winner.0);
        }
        changed
    }
}

/// Generate a continuous route. Break from the progress callback to return the partial result.
pub fn generate(
    source: &DynamicImage,
    settings: &Settings,
    progress: impl FnMut(Progress) -> ControlFlow<()>,
) -> Result<Artwork, InvalidInput> {
    generate_guided(source, settings, None, progress)
}

/// Raster-aligned guide. Red fades target darkness, green adds importance,
/// and blue weights differences across contours at two spatial scales.
pub fn generate_guided(
    source: &DynamicImage,
    settings: &Settings,
    guide: Option<&image::RgbImage>,
    progress: impl FnMut(Progress) -> ControlFlow<()>,
) -> Result<Artwork, InvalidInput> {
    generate_with_ink(source, settings, guide, None, progress)
}

/// Apply the optional raster-aligned darkness contribution after the RGB guide's fade.
pub fn generate_with_ink(
    source: &DynamicImage,
    settings: &Settings,
    guide: Option<&image::RgbImage>,
    background_ink: Option<&GrayImage>,
    mut progress: impl FnMut(Progress) -> ControlFlow<()>,
) -> Result<Artwork, InvalidInput> {
    if source.width() == 0 || source.height() == 0 {
        return Err(InvalidInput("source image must have nonzero dimensions"));
    }
    settings.validate()?;
    let canvas = Canvas::for_settings(settings.raster_size, settings);
    let mut target = canvas.target(source, settings);
    if let Some(map) = guide {
        let size = canvas.size as u32;
        if map.dimensions() != (size, size) {
            return Err(InvalidInput(
                "guide must match optimization raster dimensions",
            ));
        }
        for (tone, p) in target.iter_mut().zip(map.pixels()) {
            *tone *= 1.0 - f32::from(p[0]) / 255.0;
        }
    }
    if let Some(ink) = background_ink {
        if ink.dimensions() != (canvas.size as u32, canvas.size as u32) {
            return Err(InvalidInput(
                "background ink must match optimization raster dimensions",
            ));
        }
        for ((tone, p), &inside) in target.iter_mut().zip(ink.pixels()).zip(&canvas.inside) {
            if inside {
                *tone = (*tone + f32::from(p[0]) / 255.0).min(1.0);
            }
        }
    }
    let image = Objective::guided(&canvas, &target, settings.detail, guide);
    let initial_rmse = rmse(&canvas, &target, &image.current);
    let lines = Lines::new(&canvas, settings);
    let scratch = (0..settings.anchors.div_ceil(32))
        .map(|_| image.scratch())
        .collect();
    let mut search = Search {
        canvas: &canvas,
        lines: &lines,
        target: &target,
        settings,
        image,
        exact_after_bridge: false,
        repeats: vec![0; lines.lines.len()],
        scratch,
    };
    let mut path = vec![settings.start_anchor];
    let mut passes = 0;
    let mut exchanges = 0;
    let stop_reason = loop {
        let from = *path.last().unwrap();
        let count = path.len() - 1;
        if progress(Progress {
            segments: count,
            anchor: from,
        })
        .is_break()
        {
            break StopReason::Cancelled;
        }
        if count < settings.max_segments
            && initial_rmse > 0.0
            && let Some(m) = search.next(from, settings.max_segments - count)
        {
            search.change(m.line, true);
            path.push(m.anchor);
            continue;
        }
        if passes < settings.passes {
            let moved = search.refine(&mut path);
            let exchanged = search.exchange(&mut path, passes);
            exchanges += usize::from(exchanged);
            passes += 1;
            if moved > 0 || exchanged {
                continue;
            }
        }
        break if count == settings.max_segments {
            StopReason::SegmentLimit
        } else {
            StopReason::NoImprovingMove
        };
    };
    let segments: Vec<_> = path
        .windows(2)
        .map(|pair| {
            let (from, to) = (pair[0], pair[1]);
            Segment {
                from,
                to,
                length_mm: lines.lines[lines.id(from, to).unwrap()].length_mm,
            }
        })
        .collect();
    let total_length_mm: f64 = segments.iter().map(|s| s.length_mm).sum();
    let current = search.image.current;
    Ok(Artwork {
        target: canvas.render(&target),
        preview: canvas.render(&current),
        cache_bytes_used: lines.cache.len() * size_of::<Sample>(),
        route: Route {
            format_version: 1,
            optimizer_version: 5,
            accepted_global_passes: exchanges,
            final_rmse: rmse(&canvas, &target, &current),
            initial_rmse,
            settings: settings.clone(),
            anchors: canvas.anchors,
            path,
            segments,
            total_length_m: total_length_mm / 1000.0,
            stop_reason,
        },
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use image::{Luma, Rgba, RgbaImage};

    #[test]
    fn predicted_gain_matches_rendered_loss_at_both_scales() {
        let canvas = Canvas::new(65, 600.0, 32);
        let target = vec![0.6; 65 * 65];
        let settings = Settings {
            anchors: 32,
            raster_size: 65,
            ..Settings::default()
        };
        let lines = Lines::new(&canvas, &settings);
        let mut state = Objective::new(&canvas, &target, 0.0);
        state.change(&canvas, &lines, 0, 0.8, true);
        let loss = |state: &Objective| {
            let mut error = 0.0;
            for y in (0..65).step_by(4) {
                for x in (0..65).step_by(4) {
                    let (mut sum, mut n) = (0.0, 0);
                    for j in y..(y + 4).min(65) {
                        for i in x..(x + 4).min(65) {
                            let k = j * 65 + i;
                            if canvas.inside[k] {
                                let residual = f64::from(target[k] - state.current[k]);
                                error += 0.15 * residual.powi(2);
                                sum += residual;
                                n += 1;
                            }
                        }
                    }
                    if n > 0 {
                        error += 0.85 * sum.powi(2) / f64::from(n);
                    }
                }
            }
            error
        };
        let gain = state.gain(&canvas, &lines, 0, &target, 0.8, &mut state.scratch());
        let before = loss(&state);
        state.change(&canvas, &lines, 0, 0.8, true);
        assert!((before - loss(&state) - gain).abs() < 1e-5);
    }

    #[test]
    fn contour_gain_matches_change_in_directional_error() {
        let n = 65;
        let canvas = Canvas::new(n, 600.0, 32);
        let settings = Settings {
            raster_size: n,
            anchors: 32,
            ..Settings::default()
        };
        let target: Vec<f32> = (0..n * n)
            .map(|i| if (i % n) / 4 % 2 == 0 { 0.8 } else { 0.2 })
            .collect();
        let guide = image::RgbImage::from_pixel(n as u32, n as u32, image::Rgb([0, 0, 255]));
        let lines = Lines::new(&canvas, &settings);
        let mut baseline = Objective::new(&canvas, &target, 0.0);
        let mut guided = Objective::guided(&canvas, &target, 0.0, Some(&guide));
        let loss = |current: &[f32]| {
            let mut loss = 0.0;
            for i in 0..target.len() {
                for step in [1, 4] {
                    let j = i + step;
                    if i % n + step >= n || !canvas.inside[i] || !canvas.inside[j] {
                        continue;
                    }
                    let gradient = target[i] - target[j];
                    let w = (gradient.abs() - 0.06).clamp(0.0, 0.45);
                    loss += f64::from(w) * f64::from(gradient - (current[i] - current[j])).powi(2);
                }
            }
            loss
        };
        let mut scratch = guided.scratch();
        for line in [12, 14, 16] {
            let extra = guided.gain(&canvas, &lines, line, &target, 0.9, &mut scratch)
                - baseline.gain(&canvas, &lines, line, &target, 0.9, &mut baseline.scratch());
            let before = loss(&guided.current);
            guided.change(&canvas, &lines, line, 0.9, true);
            baseline.change(&canvas, &lines, line, 0.9, true);
            assert!((before - loss(&guided.current) - extra).abs() < 1e-5);
        }
    }

    #[test]
    fn free_nails_define_edges_lengths_and_replay_with_stable_ids() {
        let mut settings = Settings {
            anchors: 4,
            nail_positions: vec![[0., 0.], [0.5, 0.], [0., 0.5], [-0.25, -0.5]],
            raster_size: 64,
            max_segments: 24,
            thread_mm: 2.,
            ..Default::default()
        };
        let source = DynamicImage::ImageLuma8(GrayImage::from_pixel(64, 64, Luma([0])));
        let art = generate(&source, &settings, |_| ControlFlow::Continue(())).unwrap();
        assert!(!art.route.segments.is_empty());
        assert_eq!(art.route.anchors[1].x_mm, 150.);
        assert_eq!(art.route.anchors[3].y_mm, -150.);
        let canvas = Canvas::for_settings(64, &settings);
        let lines = Lines::new(&canvas, &settings);
        assert!(lines.id(0, 1).is_some());
        let collinear = [[0., 0.], [0.4, 0.], [0.8, 0.]];
        assert!(crosses_intermediate_nail(&collinear, 0, 2));
        assert!(!crosses_intermediate_nail(&collinear, 0, 1));
        assert_eq!(lines.lines[lines.id(0, 2).unwrap()].length_mm, 150.);
        let saved = serde_json::to_vec(&art.route.settings).unwrap();
        let restored: Settings = serde_json::from_slice(&saved).unwrap();
        let rendered = render_preview(&restored, &[0, 1], 128).unwrap();
        assert!(rendered.get_pixel(80, 64)[0] < 255);
        assert_eq!(rendered.get_pixel(64, 10)[0], 255);
        settings.nail_positions[3] = [0.9, 0.9];
        assert!(settings.validate().is_err());
        settings.nail_positions[3] = [0., 0.];
        assert!(settings.validate().is_err());
    }

    #[test]
    fn black_background_changes_the_path_and_leaves_the_subject_and_corners_white() {
        let settings = Settings {
            raster_size: 64,
            anchors: 32,
            max_segments: 24,
            passes: 0,
            ..Settings::default()
        };
        let source = DynamicImage::ImageLuma8(GrayImage::from_pixel(64, 64, Luma([255])));
        let mask = GrayImage::from_fn(64, 64, |x, _| Luma([if x < 32 { 255 } else { 0 }]));
        let guide =
            image::RgbImage::from_fn(64, 64, |x, y| image::Rgb([mask.get_pixel(x, y)[0], 0, 0]));
        let plain = generate_guided(&source, &settings, Some(&guide), |_| {
            ControlFlow::Continue(())
        })
        .unwrap();
        let black = generate_with_ink(&source, &settings, Some(&guide), Some(&mask), |_| {
            ControlFlow::Continue(())
        })
        .unwrap();
        assert!(plain.route.segments.is_empty());
        assert!(!black.route.segments.is_empty());
        assert_eq!(black.target.get_pixel(16, 32)[0], 0);
        assert_eq!(black.target.get_pixel(48, 32)[0], 255);
        assert_eq!(black.target.get_pixel(0, 0)[0], 255);
    }

    #[test]
    fn padded_crop_keeps_white_space_above_the_photo() {
        let settings = Settings {
            zoom: 0.5,
            center_y: 0.0,
            pad_crop: true,
            ..Settings::default()
        };
        let source = DynamicImage::ImageLuma8(GrayImage::new(100, 50));
        let canvas = Canvas::new(100, 600.0, 32);
        let target = canvas.target(&source, &settings);
        assert_eq!(target[25 * 100 + 50], 0.0);
        assert_eq!(target[60 * 100 + 50], 1.0);
    }

    #[test]
    fn global_removal_restores_one_continuous_path_with_the_exact_retained_chords() {
        let settings = Settings {
            anchors: 32,
            raster_size: 64,
            max_segments: 128,
            ..Settings::default()
        };
        let canvas = Canvas::new(64, 600.0, 32);
        let target = vec![0.0; 64 * 64];
        let image = Objective::new(&canvas, &target, 0.0);
        let lines = Lines::new(&canvas, &settings);
        let scratch = vec![image.scratch()];
        let mut search = Search {
            canvas: &canvas,
            lines: &lines,
            target: &target,
            settings: &settings,
            image,
            exact_after_bridge: false,
            repeats: vec![0; lines.lines.len()],
            scratch,
        };
        let id = lines.id(0, 16).unwrap();
        let mut path: Vec<_> = (0..=128).map(|i| i % 2 * 16).collect();
        for _ in 0..128 {
            search.change(id, true);
        }
        assert!(search.exchange(&mut path, 0));
        assert_eq!(path, [0, 16, 0]);
        assert_eq!(search.repeats[id], 2);
        assert_eq!(search.repeats.iter().sum::<u32>(), 2);
    }

    #[test]
    fn removing_one_of_two_opaque_chords_preserves_the_other() {
        let canvas = Canvas::new(65, 600.0, 32);
        let target = vec![0.4; 65 * 65];
        let settings = Settings {
            anchors: 32,
            raster_size: 65,
            thread_mm: 20.0,
            ..Settings::default()
        };
        let lines = Lines::new(&canvas, &settings);
        let mut state = Objective::new(&canvas, &target, 1.0);
        let id = lines.id(0, 16).unwrap();
        state.change(&canvas, &lines, id, 1.0, true);
        let once = state.current.clone();
        assert!(once.contains(&1.0));
        state.change(&canvas, &lines, id, 1.0, true);
        state.change(&canvas, &lines, id, 1.0, false);
        assert_eq!(once, state.current);
        state.change(&canvas, &lines, id, 1.0, false);
        assert!(state.current.iter().all(|x| x.abs() < 1e-6));
    }

    #[test]
    fn zoom_uses_the_selected_source_position_and_gamma_lifts_midtones() {
        let canvas = Canvas::new(64, 600.0, 32);
        let source = DynamicImage::ImageLuma8(GrayImage::from_fn(128, 64, |x, _| {
            Luma([if x < 64 { 64 } else { 196 }])
        }));
        let mut settings = Settings {
            zoom: 2.0,
            center_x: 0.0,
            ..Settings::default()
        };
        let dark = canvas.target(&source, &settings)[32 * 64 + 32];
        settings.center_x = 100.0;
        let light = canvas.target(&source, &settings)[32 * 64 + 32];
        assert!(dark > light + 0.4);
        settings.gamma = 0.5;
        assert!(canvas.target(&source, &settings)[32 * 64 + 32] < light);
    }

    #[test]
    fn preprocessing_crops_the_center_and_composites_transparency_on_white() {
        let mut source = RgbaImage::from_pixel(128, 64, Rgba([0, 0, 0, 255]));
        for x in 32..96 {
            for y in 0..64 {
                source.put_pixel(x, y, Rgba([0, 0, 0, 0]));
            }
        }
        let canvas = Canvas::new(64, 600.0, 32);
        assert!((canvas.anchors[0].y_mm + 300.0).abs() < 1e-9);
        assert!((canvas.anchors[8].x_mm - 300.0).abs() < 1e-9);
        let target = canvas.target(&DynamicImage::ImageRgba8(source), &Settings::default());
        assert!(target.into_iter().all(|darkness| darkness == 0.0));
    }

    #[test]
    fn route_is_continuous_and_unchanged_when_rasterizing_on_demand() {
        let source = DynamicImage::ImageLuma8(GrayImage::from_fn(72, 72, |x, y| {
            let stripe = x.abs_diff(35) < 7 || y.abs_diff(35) < 3;
            Luma([if stripe { 32 } else { 220 }])
        }));
        let mut settings = Settings {
            anchors: 40,
            raster_size: 72,
            max_segments: 60,
            min_anchor_gap: 2,
            ..Settings::default()
        };
        let cached = generate(&source, &settings, |_| ControlFlow::Continue(())).unwrap();
        settings.cache_mib = 0;
        let uncached = generate(&source, &settings, |_| ControlFlow::Continue(())).unwrap();
        assert!(cached.cache_bytes_used > 0);
        assert_eq!(uncached.cache_bytes_used, 0);
        let route = cached.route;
        assert_eq!(route.path, uncached.route.path);
        assert_eq!(cached.preview, uncached.preview);
        assert_eq!(route.segments.len(), settings.max_segments);
        assert!(route.final_rmse < route.initial_rmse);
        assert_eq!(route.path[0], settings.start_anchor);
        assert_eq!(route.path.len(), route.segments.len() + 1);
        for (anchors, segment) in route.path.windows(2).zip(&route.segments) {
            assert_eq!(anchors, [segment.from, segment.to]);
            let gap = segment.from.abs_diff(segment.to);
            assert!(gap.min(settings.anchors - gap) >= settings.min_anchor_gap);
            assert!(segment.length_mm > 0.0 && segment.length_mm <= settings.diameter_mm + 1e-9);
        }
        // Independently replay the exported chords, including changed interior anchors.
        let canvas = Canvas::for_settings(settings.raster_size, &settings);
        let mut replay = vec![0.0; canvas.inside.len()];
        for segment in &route.segments {
            canvas.rasterize(
                canvas.points[segment.from],
                canvas.points[segment.to],
                settings.thread_mm / settings.diameter_mm * (canvas.size - 1) as f64,
                |sample| {
                    let pixel = &mut replay[sample.index as usize];
                    *pixel += (1.0 - *pixel) * settings.opacity * sample.coverage;
                },
            );
        }
        assert!(
            canvas
                .render(&replay)
                .as_raw()
                .iter()
                .zip(cached.preview.as_raw())
                .all(|(a, b)| a.abs_diff(*b) <= 1)
        );
        let length: f64 = route.segments.iter().map(|segment| segment.length_mm).sum();
        assert!((route.total_length_m * 1000.0 - length).abs() < 1e-8);
    }

    #[test]
    fn white_target_stops_without_adding_thread() {
        let source = DynamicImage::ImageLuma8(GrayImage::from_pixel(64, 64, Luma([255])));
        let settings = Settings {
            anchors: 32,
            raster_size: 64,
            ..Settings::default()
        };
        let art = generate(&source, &settings, |_| ControlFlow::Continue(())).unwrap();
        assert_eq!(art.route.stop_reason, StopReason::NoImprovingMove);
        assert_eq!(art.route.path, [0]);
        assert!(art.route.segments.is_empty());
        assert_eq!(art.preview, art.target);
    }
}

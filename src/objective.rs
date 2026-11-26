//! Score the rendered thread at pixel resolution and at viewing distance.
use crate::{Lines, raster::Canvas};

const CELL_SIDE: usize = 4;
const TONE_WEIGHT: f64 = 0.85;

pub(super) fn cell_index(x: usize, y: usize, size: usize) -> usize {
    y / CELL_SIDE * size.div_ceil(CELL_SIDE) + x / CELL_SIDE
}

pub(super) struct Objective {
    pub current: Vec<f32>,
    cells: Vec<usize>,
    depth: Vec<f64>,
    opaque: Vec<u32>,
    weights: Vec<f32>,
    neighbors: Vec<Vec<(usize, f32)>>,
    cell_weights: Vec<f64>,
    target_means: Vec<f64>,
    inverse_counts: Vec<f64>,
    // Sum of target minus render within each cell, excluding the circular border.
    residual: Vec<f64>,
}

pub(super) struct Scratch {
    delta: Vec<f64>,
    touched: Vec<usize>,
    pixel_delta: Vec<f32>,
    edge_pixels: Vec<usize>,
}

impl Objective {
    #[cfg(test)]
    pub fn new(canvas: &Canvas, target: &[f32], emphasis: f32) -> Self {
        Self::guided(canvas, target, emphasis, None, 0.0)
    }

    pub fn guided(
        canvas: &Canvas,
        target: &[f32],
        emphasis: f32,
        guide: Option<&image::RgbImage>,
        white_protection: f32,
    ) -> Self {
        // Normalize the local mean by the circle mask so the rim is not a false edge.
        let image = canvas.render_area(target, canvas.size, canvas.size);
        let mask = image::GrayImage::from_fn(canvas.size as u32, canvas.size as u32, |x, y| {
            image::Luma([if canvas.inside[y as usize * canvas.size + x as usize] {
                255
            } else {
                0
            }])
        });
        let radius = canvas.size as f32 / 48.0;
        let blurred = image::imageops::blur(&image, radius);
        let coverage = image::imageops::blur(&mask, radius);
        let mut weights: Vec<_> = target
            .iter()
            .zip(blurred.as_raw())
            .zip(coverage.as_raw())
            .map(|((&tone, &light), &area)| {
                let mean = f32::from(255 - light) / f32::from(area.max(1));
                let near_white = ((0.14 - tone) / 0.14).clamp(0.0, 1.0);
                (1.0 + emphasis * ((tone - mean).abs() * 5.0).min(2.0).powi(2))
                    * (1.0 + white_protection * near_white)
            })
            .collect();
        let mut neighbors = Vec::new();
        if let Some(map) = guide {
            for (weight, p) in weights.iter_mut().zip(map.pixels()) {
                *weight *= 1.0 + 5.0 * f32::from(p[1]) / 255.0;
            }
            if map.pixels().any(|p| p[2] != 0) {
                neighbors = vec![Vec::new(); target.len()];
                // Central differences across fine and broad transitions. Each pair is
                // stored in both directions so candidate gains include both endpoints.
                for y in 0..canvas.size {
                    for x in 0..canvas.size {
                        let i = y * canvas.size + x;
                        if !canvas.inside[i] {
                            continue;
                        }
                        for step in [1, 4] {
                            for (nx, ny) in [(x + step, y), (x, y + step)] {
                                if nx >= canvas.size || ny >= canvas.size {
                                    continue;
                                }
                                let j = ny * canvas.size + nx;
                                let contrast = (target[i] - target[j]).abs();
                                if !canvas.inside[j] || contrast <= 0.06 {
                                    continue;
                                }
                                let strength = map.as_raw()[3 * i + 2].min(map.as_raw()[3 * j + 2]);
                                let w = (contrast - 0.06).min(0.45) * f32::from(strength) / 255.0;
                                if w > 0.0 {
                                    neighbors[i].push((j, w));
                                    neighbors[j].push((i, w));
                                }
                            }
                        }
                    }
                }
            }
        }
        let cols = canvas.size.div_ceil(CELL_SIDE);
        let mut cell_weights = vec![0.0; cols * cols];
        let mut counts = vec![0usize; cols * cols];
        let mut residual = vec![0.0; counts.len()];
        let mut cells = Vec::with_capacity(target.len());
        for (index, &tone) in target.iter().enumerate() {
            let cell = cell_index(index % canvas.size, index / canvas.size, canvas.size);
            cells.push(cell);
            if canvas.inside[index] {
                counts[cell] += 1;
                cell_weights[cell] += f64::from(weights[index]);
                residual[cell] += f64::from(tone);
            }
        }
        for (weight, &n) in cell_weights.iter_mut().zip(&counts) {
            *weight /= n.max(1) as f64;
        }
        Self {
            depth: vec![0.0; target.len()],
            opaque: vec![0; target.len()],
            weights,
            neighbors,
            target_means: residual
                .iter()
                .zip(&counts)
                .map(|(sum, n)| sum / (*n).max(1) as f64)
                .collect(),
            cell_weights,
            current: vec![0.0; target.len()],
            cells,
            residual,
            inverse_counts: counts.into_iter().map(|n| 1.0 / n.max(1) as f64).collect(),
        }
    }

    pub fn coarse_score(&self, cells: &[u32]) -> f64 {
        cells
            .iter()
            .map(|&cell| {
                let i = cell as usize;
                let error = self.residual[i] * self.inverse_counts[i];
                let transmission = 1.0 - self.target_means[i] + error;
                self.cell_weights[i] * error * transmission
            })
            .sum()
    }

    pub fn scratch(&self) -> Scratch {
        Scratch {
            delta: vec![0.0; self.residual.len()],
            touched: Vec::new(),
            pixel_delta: vec![0.0; self.neighbors.len()],
            edge_pixels: Vec::new(),
        }
    }

    pub fn gain(
        &self,
        canvas: &Canvas,
        lines: &Lines,
        line: usize,
        target: &[f32],
        opacity: f32,
        scratch: &mut Scratch,
    ) -> f64 {
        let mut fine = 0.0;
        lines.visit(canvas, line, |sample| {
            let i = sample.index as usize;
            let delta = (1.0 - self.current[i]) * (sample.coverage * opacity);
            if delta > 0.0 {
                fine += f64::from(
                    self.weights[i] * delta * (2.0 * (target[i] - self.current[i]) - delta),
                );
                if !self.neighbors.is_empty() && !self.neighbors[i].is_empty() {
                    scratch.pixel_delta[i] = delta;
                    scratch.edge_pixels.push(i);
                }
                let cell = self.cells[i];
                if scratch.delta[cell] == 0.0 {
                    scratch.touched.push(cell);
                }
                scratch.delta[cell] += f64::from(delta);
            }
        });
        let mut tone = 0.0;
        for cell in scratch.touched.drain(..) {
            let delta = std::mem::take(&mut scratch.delta[cell]);
            tone += self.cell_weights[cell]
                * self.inverse_counts[cell]
                * delta
                * (2.0 * self.residual[cell] - delta);
        }
        let mut contour = 0.0;
        for &i in &scratch.edge_pixels {
            let d = scratch.pixel_delta[i];
            let residual = target[i] - self.current[i];
            for &(j, w) in &self.neighbors[i] {
                contour += f64::from(
                    w * d
                        * (2.0 * (residual - (target[j] - self.current[j])) - d
                            + scratch.pixel_delta[j]),
                );
            }
        }
        for i in scratch.edge_pixels.drain(..) {
            scratch.pixel_delta[i] = 0.0;
        }
        fine * (1.0 - TONE_WEIGHT) + tone * TONE_WEIGHT + contour
    }

    // Optical depths add. Keep fully opaque hits separately so removing one
    // chord also works when another chord still blacks out the same pixel.
    pub fn change(&mut self, canvas: &Canvas, lines: &Lines, line: usize, opacity: f32, add: bool) {
        lines.visit(canvas, line, |sample| {
            let i = sample.index as usize;
            let alpha = f64::from(sample.coverage * opacity);
            if alpha >= 1.0 {
                if add {
                    self.opaque[i] += 1;
                } else {
                    self.opaque[i] -= 1;
                }
            } else {
                let log_transmission = (-alpha).ln_1p();
                self.depth[i] += if add {
                    -log_transmission
                } else {
                    log_transmission
                };
            }
            let darkness = if self.opaque[i] > 0 {
                1.0
            } else {
                -(-self.depth[i].max(0.0)).exp_m1() as f32
            };
            self.residual[self.cells[i]] -= f64::from(darkness) - f64::from(self.current[i]);
            self.current[i] = darkness;
        });
    }
}

//! Refine chord multiplicities independently of winding order; restore one Euler trail.
use super::{Lines, MIN_GAIN, Search};

fn euler_path(lines: &Lines, counts: &[u32], start: usize) -> Vec<usize> {
    let mut remaining = counts.to_vec();
    let mut cursor = vec![0; lines.moves.len()];
    let mut stack = vec![start];
    let mut reversed = Vec::new();
    while let Some(&pin) = stack.last() {
        let moves = &lines.moves[pin];
        while cursor[pin] < moves.len() && remaining[moves[cursor[pin]].line] == 0 {
            cursor[pin] += 1;
        }
        if let Some(m) = moves.get(cursor[pin]) {
            remaining[m.line] -= 1;
            stack.push(m.anchor);
        } else {
            reversed.push(stack.pop().unwrap());
        }
    }
    reversed.reverse();
    reversed
}

impl Search<'_> {
    // Reversing b..c changes only the two cut edges (a,b) and (c,d).
    // Nail loads and both path endpoints are invariant.
    pub(super) fn rewire(&mut self, path: &mut [usize], pass: usize) {
        if path.len() < 4 { return; }
        let edges = path.len() - 1;
        let mut rng = 0x8471_u64.wrapping_add(pass as u64);
        for left in 0..edges - 2 {
            for _ in 0..16 {
                rng = rng.wrapping_mul(6364136223846793005).wrapping_add(1);
                let right = left + 2 + ((rng >> 32) as usize % (edges - left - 2));
                let (a,b,c,d) = (path[left], path[left+1], path[right], path[right+1]);
                if a == d || b == c { continue; }
                let (Some(first), Some(second)) = (self.lines.id(a,c), self.lines.id(b,d)) else { continue; };
                let old_first = self.lines.id(a,b).unwrap();
                let old_second = self.lines.id(c,d).unwrap();
                self.change(old_first,false);
                self.change(old_second,false);
                let old = self.pair_gain(old_first,old_second);
                let new = self.pair_gain(first,second);
                if new > old + MIN_GAIN {
                    self.change(first,true);
                    self.change(second,true);
                    path[left+1..=right].reverse();
                } else {
                    self.change(old_first,true);
                    self.change(old_second,true);
                }
            }
        }
    }

    // Cost improvement from removing one copy, including the repeat penalty.
    fn removal_gain(&mut self, id: usize) -> f64 {
        self.change(id, false);
        let gain = -self.gain(id);
        self.change(id, true);
        gain
    }

    fn restore_counts(&mut self, saved: &[u32]) {
        for (id, &count) in saved.iter().enumerate() {
            while self.repeats[id] > count {
                self.change(id, false);
            }
            while self.repeats[id] < count {
                self.change(id, true);
            }
        }
    }

    pub(super) fn exchange(&mut self, path: &mut Vec<usize>, round: usize) -> bool {
        let pins = self.settings.anchors;
        let budget = self.settings.max_segments;
        let mut count = path.len() - 1;
        if count < pins || budget < 2 * pins {
            return false;
        }
        let saved = self.repeats.clone();
        let mut tree = vec![false; saved.len()];
        let mut connected = vec![false; pins];
        connected[path[0]] = true;
        // Keeping one copy of each first-arrival edge guarantees connectivity.
        for pair in path.windows(2) {
            if !connected[pair[1]] {
                tree[self.lines.id(pair[0], pair[1]).unwrap()] = true;
                connected[pair[1]] = true;
            }
        }
        // Shuffling avoids favoring a particular arc of the circle.
        let mut ids: Vec<_> = (0..saved.len()).collect();
        let mut seed = 0x6305_9bc1_u32.wrapping_add(round as u32);
        for i in (1..ids.len()).rev() {
            seed = seed.wrapping_mul(1664525).wrapping_add(1013904223);
            ids.swap(i, seed as usize % (i + 1));
        }

        let mut net = 0.0;
        // Make room first, including for the parity repair. Re-evaluate each
        // removal on the actual optical state; coincident threads do not add linearly.
        for &id in &ids {
            while self.repeats[id] > u32::from(tree[id]) {
                self.change(id, false);
                let gain = -self.gain(id);
                if gain <= MIN_GAIN {
                    self.change(id, true);
                    break;
                }
                net += gain;
                count -= 1;
            }
        }
        let addition_limit = budget - pins.div_ceil(2);
        ids.truncate(80_000);
        if count >= addition_limit {
            // At the hard cap, additions need a temporary vacancy. Release the
            // least useful removable copies and reconsider the strongest alternatives first.
            let removable: Vec<_> = (0..saved.len())
                .filter(|&id| self.repeats[id] > u32::from(tree[id]))
                .collect();
            let mut losses: Vec<_> = removable
                .into_iter()
                .map(|id| (self.removal_gain(id), id))
                .collect();
            losses.sort_unstable_by(|a, b| b.0.total_cmp(&a.0).then(a.1.cmp(&b.1)));
            let vacancy = count - addition_limit + pins.min(count / 8);
            for (_, id) in losses.into_iter().take(vacancy) {
                self.change(id, false);
                net -= self.gain(id);
                count -= 1;
            }
            let mut ranked: Vec<_> = ids.into_iter().map(|id| (self.gain(id), id)).collect();
            ranked.sort_unstable_by(|a, b| b.0.total_cmp(&a.0).then(a.1.cmp(&b.1)));
            ids = ranked.into_iter().map(|(_, id)| id).collect();
        }
        for id in ids {
            if count >= addition_limit {
                break;
            }
            let (a, b) = self.lines.lines[id].endpoints;
            if (!connected[a] && !connected[b]) || !self.can_add(id) {
                continue;
            }
            let gain = self.gain(id);
            if gain > MIN_GAIN {
                self.change(id, true);
                if !connected[a] || !connected[b] {
                    tree[id] = true;
                    connected[a] = true;
                    connected[b] = true;
                }
                net += gain;
                count += 1;
            }
        }
        // Pair parity defects. Preserve the original start and end. Actual
        // connector costs count against the same objective and segment budget.
        let mut defect = vec![false; pins];
        for (id, &n) in self.repeats.iter().enumerate() {
            if n % 2 == 1 {
                let (a, b) = self.lines.lines[id].endpoints;
                defect[a] ^= true;
                defect[b] ^= true;
            }
        }
        defect[path[0]] ^= true;
        defect[*path.last().unwrap()] ^= true;
        for a in 0..pins {
            if !defect[a] {
                continue;
            }
            let mut best = None;
            for m in &self.lines.moves[a] {
                if !defect[m.anchor] {
                    continue;
                }
                let id = m.line;
                let add = if self.can_add(id) {
                    self.gain(id)
                } else {
                    f64::NEG_INFINITY
                };
                let remove = if self.repeats[id] > u32::from(tree[id]) {
                    self.removal_gain(id)
                } else {
                    f64::NEG_INFINITY
                };
                let gain = add.max(remove);
                if gain.is_finite() && best.is_none_or(|(score, _, _, _)| gain > score) {
                    best = Some((gain, id, m.anchor, add >= remove));
                }
            }
            if let Some((gain, id, b, add)) = best {
                self.change(id, add);
                net += gain;
                count = if add { count + 1 } else { count - 1 };
                defect[b] = false;
            } else {
                // Nearby pins may not permit a direct chord. Try a legal two-edge bridge.
                let Some(b) = (a + 1..pins).find(|&v| defect[v]) else {
                    unreachable!()
                };
                let mut bridge = None;
                for m in &self.lines.moves[a] {
                    if let Some(second) = self.lines.id(m.anchor, b) {
                        let gain = self.pair_gain(m.line, second);
                        if gain.is_finite() && bridge.is_none_or(|(score, _, _)| gain > score) {
                            bridge = Some((gain, m.line, second));
                        }
                    }
                }
                let Some((gain, first, second)) = bridge else {
                    self.restore_counts(&saved);
                    return false;
                };
                self.change(first, true);
                self.change(second, true);
                count += 2;
                net += gain;
                defect[b] = false;
            }
            defect[a] = false;
        }
        if count > budget || net <= MIN_GAIN {
            self.restore_counts(&saved);
            return false;
        }
        *path = euler_path(self.lines, &self.repeats, path[0]);
        debug_assert_eq!(path.len(), count + 1);
        true
    }
}

//! When to restart a crashed engine: after 1, 5, then 30 s; give up after 5 crashes within 10 minutes.

use std::collections::VecDeque;
use std::time::{Duration, Instant};

const DELAYS: [u64; 3] = [1, 5, 30]; // seconds, by the number of recent crashes; the last one repeats
const WINDOW: Duration = Duration::from_secs(10 * 60);
const GIVE_UP_AFTER: usize = 5;

pub struct RestartPolicy {
    crashes: VecDeque<Instant>, // within the last WINDOW
}

impl RestartPolicy {
    pub fn new() -> Self {
        RestartPolicy { crashes: VecDeque::new() }
    }

    /// Record a crash at `at`: how long to wait before restarting, or None to give up.
    pub fn on_crash(&mut self, at: Instant) -> Option<Duration> {
        self.crashes.push_back(at);
        while self.crashes.front().is_some_and(|first| at.duration_since(*first) > WINDOW) {
            self.crashes.pop_front();
        }
        if self.crashes.len() >= GIVE_UP_AFTER {
            return None;
        }
        Some(Duration::from_secs(DELAYS[(self.crashes.len() - 1).min(DELAYS.len() - 1)]))
    }
}

impl Default for RestartPolicy {
    fn default() -> Self {
        Self::new()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn restart_delays_are_1_5_30_then_30() {
        let mut policy = RestartPolicy::new();
        let start = Instant::now();
        let delays: Vec<_> = (0..4).map(|i| policy.on_crash(start + Duration::from_secs(i)).unwrap()).collect();
        assert_eq!(delays, [1, 5, 30, 30].map(Duration::from_secs));
    }

    #[test]
    fn restart_gives_up_after_5_crashes_in_10_minutes() {
        let mut policy = RestartPolicy::new();
        let start = Instant::now();
        for i in 0..4 {
            assert!(policy.on_crash(start + Duration::from_secs(i * 60)).is_some());
        }
        assert_eq!(policy.on_crash(start + Duration::from_secs(9 * 60)), None);
    }

    #[test]
    fn old_crashes_do_not_count() {
        let mut policy = RestartPolicy::new();
        let start = Instant::now();
        for i in 0..8 {
            assert_eq!(policy.on_crash(start + Duration::from_secs(i * 11 * 60)), Some(Duration::from_secs(1)));
        }
    }
}

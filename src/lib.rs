// SPDX-License-Identifier: Apache-2.0
//! SigmaOS-try-windows — SigmaOS try-windows component inspired by Linux Mint / Omarchy / Arch Linux.
//!
//! Ordered execution plan: validated steps executed fail-fast, first error aborts.
//! Principles: single responsibility, fail-fast, defensive programming,
//! component isolation, stable API, deterministic tests.

#![forbid(unsafe_code)]

use std::fmt;
/// Errors for `PlanError` operations. Fail-fast: invalid input is rejected.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum PlanError {
    /// Plan step description must not be empty
    EmptyStep,
    /// Cannot run an empty plan
    EmptyPlan,
    /// A plan step failed during execution
    StepFailed(String),
}

impl fmt::Display for PlanError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::EmptyStep => write!(f, "plan step description must not be empty"),
            Self::EmptyPlan => write!(f, "cannot run an empty plan"),
            Self::StepFailed(x) => write!(f, "a plan step failed during execution: {x}"),
        }
    }
}

impl std::error::Error for PlanError {}

/// An ordered list of steps forming an executable plan.
#[derive(Debug, Default)]
pub struct Plan {
    steps: Vec<String>,
}

impl Plan {
    /// Empty plan.
    pub fn new() -> Self { Self::default() }

    /// Append a step; fails on empty description.
    pub fn add_step(&mut self, step: impl Into<String>) -> Result<(), PlanError> {
        let step = step.into();
        if step.trim().is_empty() { return Err(PlanError::EmptyStep); }
        self.steps.push(step);
        Ok(())
    }

    /// Number of steps.
    pub fn len(&self) -> usize { self.steps.len() }

    /// Whether the plan has no steps.
    pub fn is_empty(&self) -> bool { self.steps.is_empty() }

    /// Validate the plan is non-empty.
    pub fn validate(&self) -> Result<(), PlanError> {
        if self.steps.is_empty() { Err(PlanError::EmptyPlan) } else { Ok(()) }
    }

    /// Execute steps in order via `exec`; first failure aborts and is
    /// wrapped in `StepFailed`. Returns the list of completed step names.
    pub fn run<F>(&self, mut exec: F) -> Result<Vec<String>, PlanError>
    where
        F: FnMut(&str) -> Result<(), String>,
    {
        self.validate()?;
        let mut done = Vec::with_capacity(self.steps.len());
        for step in &self.steps {
            exec(step).map_err(|e| PlanError::StepFailed(format!("{step}: {e}")))?;
            done.push(step.clone());
        }
        Ok(done)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn plan() -> Plan {
        let mut p = Plan::new();
        p.add_step("partition").unwrap();
        p.add_step("install base").unwrap();
        p
    }

    #[test]
    fn run_executes_in_order() {
        let p = plan();
        let done = p.run(|_| Ok(())).unwrap();
        assert_eq!(done, vec!["partition".to_string(), "install base".to_string()]);
    }

    #[test]
    fn failure_aborts_plan() {
        let p = plan();
        let res = p.run(|s| if s == "install base" { Err("boom".into()) } else { Ok(()) });
        assert!(matches!(res, Err(PlanError::StepFailed(_))));
    }

    #[test]
    fn empty_step_rejected() {
        assert!(matches!(Plan::new().add_step(" "), Err(PlanError::EmptyStep)));
    }

    #[test]
    fn empty_plan_run_fails() {
        assert_eq!(Plan::new().run(|_| Ok(())), Err(PlanError::EmptyPlan));
    }

}

/// Backwards-compatible component handle (kept for existing callers/tests).
#[derive(Debug, Default)]
pub struct Component {
    name: &'static str,
}

impl Component {
    /// Create the component handle.
    pub fn new() -> Self {
        Self { name: "SigmaOS Component" }
    }
}

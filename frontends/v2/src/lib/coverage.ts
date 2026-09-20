// The frozen coverage-cell vocabulary (orchestrator/coverage.py, CONTRACT_VERSION 1) as the UI
// lists it, and the one humanizer: the stored keys never change, only their display.
export const SHOTS = ["face_closeup", "portrait", "waist_up", "full_body"];
export const ANGLES = ["front", "three_quarter_left", "three_quarter_right", "profile_left", "profile_right", "back"];
export const EXPRESSIONS = ["neutral", "smile", "serious", "sad", "surprised"];

export const nice = (s?: string | null) => (s ?? "").replace(/_/g, " ").trim();

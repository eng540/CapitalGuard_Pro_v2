"""Validate Railway Config as Code types before deployment."""
from pathlib import Path
import tomllib


config_path = Path(__file__).resolve().parents[1] / "railway.toml"
with config_path.open("rb") as handle:
    config = tomllib.load(handle)

deploy = config.get("deploy", {})
for key in ("healthcheckTimeout", "restartPolicyMaxRetries", "overlapSeconds", "drainingSeconds"):
    value = deploy.get(key)
    if not isinstance(value, int) or isinstance(value, bool):
        raise SystemExit(f"deploy.{key} must be a TOML integer, got {value!r}")

if deploy.get("healthcheckTimeout", 0) <= 0:
    raise SystemExit("deploy.healthcheckTimeout must be positive")
if deploy.get("overlapSeconds", 0) < 0 or deploy.get("drainingSeconds", 0) < 0:
    raise SystemExit("deploy overlap/draining values must be non-negative")

print("railway.toml type validation passed")

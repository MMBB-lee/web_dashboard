"""E synthetic results -> real D mapping/schema -> real B pure TS validators.

Uses Node's TypeScript stripping, no frontend dependencies, DOM or mocked HTTP.
"""
import argparse
import json
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    sys.dont_write_bytecode = True
    sys.path[:0] = [str(ROOT.parent / "api_platform"), str(ROOT)]
    from app.schemas.videos import AnalysisConfig, CountsResponse, ShortForecastResponse
    from app.services.e_bridge import _normalise_counts, _normalise_short_forecast, _roi_for_e

    from intelligent_analysis.contracts import VideoAnalysisConfig

    run = ROOT / "artifacts" / ("b-contract-" + uuid4().hex[:8])
    run.mkdir()
    fixture = json.loads((ROOT / "docs/fixtures/visible-people-demo.json").read_text(encoding="utf-8"))
    e_counts = fixture["counts"]
    mapped = _normalise_counts(e_counts, "5min")
    metadata = e_counts["analysis"]
    public = CountsResponse.model_validate({**metadata, **mapped,
        "points": [p for p in mapped["points"] if p["grain"] == "5min"]}).model_dump(mode="json")
    public["points"][0].update(value=1.5)
    forecasts = json.loads((ROOT / "docs/acceptance/E061-C021a2-forecast-storage.json").read_text(encoding="utf-8"))
    ready = forecasts["horizons"]["15"]
    mapped_forecast = ShortForecastResponse.model_validate(_normalise_short_forecast(ready)).model_dump(mode="json")
    cases = {"counts": public, "forecast": mapped_forecast, "cutoff": ready["training_cutoff"],
             "analysis_id": public["analysis_id"]}
    (run / "cases.json").write_text(json.dumps(cases), encoding="utf-8")
    script = '''
import fs from 'node:fs';
import assert from 'node:assert/strict';
import {pathToFileURL} from 'node:url';
const [source, input] = process.argv.slice(2);
const {readCounts,readForecast,analysisRequest}=await import(pathToFileURL(source));
const c=JSON.parse(fs.readFileSync(input,'utf8'));
const result=readCounts(c.counts,'5min',c.analysis_id,true);
assert.equal(result.points[0].value,1.5);
assert.equal(result.manual_validation,null);
const missing=structuredClone(c.counts);missing.points[0].value=null;
missing.points[0].quality_status='unavailable';missing.points[0].sample_count=0;
assert.equal(readCounts(missing,'5min',c.analysis_id,true).points[0].value,null);
assert.throws(()=>readCounts(c.counts,'5min','wrong-id',true));
const bad=structuredClone(c.counts);bad.confidence_threshold=.6;
assert.throws(()=>readCounts(bad,'5min',c.analysis_id,true));
assert.equal(readForecast(c.forecast,c.cutoff,15).forecast_points.length,3);
const insufficient={...c.forecast,status:'data_insufficient',forecast_points:[]};
assert.equal(readForecast(insufficient,c.cutoff,15).forecast_points.length,0);
console.log(JSON.stringify({checks:{fractional_median:true,missing_not_zero:true,
 manual_validation_null:true,wrong_identity_rejected:true,threshold_mismatch_rejected:true,
 forecast_mapping:true,insufficient_empty:true},request:analysisRequest([[0,0],[1,0],[1,1],[0,1]])}));
'''
    script_path = run / "verify.mjs"
    script_path.write_text(script, encoding="utf-8")
    b_source = ROOT.parent / "web_interactions/web_interactions/src/video/contract.ts"
    completed = subprocess.run(["node", str(script_path), str(b_source), str(run / "cases.json")],
        check=True, capture_output=True, text=True)
    result = json.loads(completed.stdout)
    d_request = AnalysisConfig.model_validate(result.pop("request")).model_dump(exclude_none=True)
    d_request["roi"] = _roi_for_e(d_request["roi"])
    VideoAnalysisConfig.model_validate(d_request)
    result["checks"]["B_request_accepted_by_D_and_E"] = True
    result.update(status="synthetic_contract_passed", data_kind="synthetic",
        B_commit=subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT.parent / "web_interactions", text=True).strip(),
        D_commit=subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT.parent / "api_platform", text=True).strip(),
        browser_verified=False, real_video_verified=False, HTTP_verified=False,
        run_relative_to_e=run.relative_to(ROOT).as_posix())
    (args.output or ROOT / "docs/acceptance/E061-B-D-contract.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result))


if __name__ == "__main__":
    main()

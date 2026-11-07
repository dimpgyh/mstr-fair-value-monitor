"""Ensure instant browser calculations agree with the canonical Python model."""

import copy
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import unittest
from test_valuation import config, market
from app.valuation_engine import build_valuation


class SimulationParityTests(unittest.TestCase):
    def test_browser_matches_server_with_funding_conversion_and_insolvency(self):
        node = shutil.which("node")
        if not node:
            spec = importlib.util.find_spec("playwright")
            if spec:
                candidate = Path(spec.origin).parent / "driver" / "node"
                if candidate.exists():
                    node = str(candidate)
        if not node:
            self.skipTest(
                "Node required for browser/server parity; CI installs Node 22"
            )
        cases = []
        for updates in [
            {},
            {
                "new_equity_usd": 10000000,
                "equity_issue_price": 120,
                "new_debt_usd": 5000000,
                "new_preferred_usd": 3000000,
                "converted_debt": 2000000,
                "converted_preferred": 1000000,
                "conversion_new_shares": 30000,
                "conversion_carry_relief": 100000,
                "funding_fee_pct": 2,
            },
            {"total_debt": 300000000},
        ]:
            cfg = config() | updates
            observed = build_valuation(cfg, market())
            for btc in (10000, 75693, 100000, 300000):
                hypothetical = copy.deepcopy(observed["market"])
                hypothetical["btc"]["price"] = btc
                expected = build_valuation(cfg, hypothetical)
                cases.append(
                    {"observation": observed, "btc": btc, "expected": expected}
                )
        script = r"""
require(process.argv[1]);
let input='';process.stdin.on('data',c=>input+=c);
process.stdin.on('end',()=>{
 const assert=require('node:assert/strict');
 function compare(a,b,path=''){
  if(typeof b==='number')assert.ok(Math.abs(a-b)<=Math.max(1,Math.abs(b))*1e-10,`${path}: ${a} != ${b}`);
  else if(b&&typeof b==='object')for(const key of Object.keys(b))compare(a[key],b[key],path+'.'+key);
  else assert.equal(a,b,path);
 }
 for(const c of JSON.parse(input)){
  const before=JSON.stringify(c.observation);
  const result=simulateValuation(c.observation,c.btc);
  for(const key of ['backing','scenarios','relative','sensitivity'])compare(result[key],c.expected[key],key);
  assert.equal(JSON.stringify(c.observation),before,'Source observation mutated');
  assert.deepEqual(result.market,c.observation.market,'Hypothetical price mislabeled as market data');
 }
 assert.throws(()=>simulateValuation({},0),RangeError);
 assert.throws(()=>simulateValuation({},Infinity),RangeError);
});
"""
        completed = subprocess.run(
            [node, "-e", script, str(Path("app/static/simulation.js").resolve())],
            input=json.dumps(cases),
            text=True,
            capture_output=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)

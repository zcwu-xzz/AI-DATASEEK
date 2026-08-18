import importlib.util
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("tabular_operations", ROOT / "tools" / "tabular" / "operations.py")
OPS = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(OPS)


def _xlsx(path):
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        pd.DataFrame({"date":["2024-01-01","2024-01-02","2024-01-03"],"region":["A","A","B"],"value":[1.0,2.0,4.0]}).to_excel(writer,index=False,sheet_name="observations")


def test_inspect_extract_profile_schema_and_recommend(tmp_path):
    path=tmp_path/"sample.xlsx"; _xlsx(path)
    assert OPS.workbook_inspect({"input_path":str(path)})["summary"]["sheets"][0]["name"]=="observations"
    extracted=OPS.table_extract({"input_path":str(path),"sheet":"observations","max_rows":10})
    assert extracted["summary"]["rows"]==3
    profile=OPS.table_profile({"input_path":str(path),"sheet":"observations"})
    assert profile["summary"]["columns"]["value"]["numeric"]["max"]==4.0
    schema=OPS.schema_infer({"input_path":str(path),"sheet":"observations","sample_rows":20})
    assert "value" in schema["summary"]["column_roles"]
    recommended=OPS.chart_recommend({"input_path":str(path),"sheet":"observations"})
    assert recommended["summary"]["recommendations"]


def test_filter_join_formula_and_visualize(tmp_path,monkeypatch):
    monkeypatch.setenv("AI_DATASEEK_OUTPUT_ROOT",str(tmp_path)); left=tmp_path/"left.xlsx"; right=tmp_path/"right.csv"; _xlsx(left)
    pd.DataFrame({"date":["2024-01-01","2024-01-02","2024-01-04"],"region":["A","A","C"],"value":[1.0,3.0,5.0]}).to_csv(right,index=False)
    aggregate=OPS.filter_aggregate({"input_path":str(left),"sheet":"observations","filters":[{"column":"value","operator":"gt","value":1}],"group_by":["region"],"aggregations":{"value":"mean"},"output_path":str(tmp_path/"aggregate.csv")})
    assert aggregate["summary"]["result_rows"]==2
    compared=OPS.join_compare({"left_path":str(left),"right_path":str(right),"left_sheet":"observations","keys":["date"],"mode":"compare","how":"outer","output_path":str(tmp_path/"compare.csv")})
    assert compared["summary"]["counts"]["changed"]==1
    audit=OPS.formula_audit({"input_path":str(left),"max_formulas":10})
    assert audit["summary"]["formula_count"]==0
    chart=OPS.visualize({"input_path":str(left),"sheet":"observations","chart":"line","x":"date","y":["value"],"output_path":str(tmp_path/"chart.png")})
    assert chart["artifacts"][0]["size_bytes"]>0

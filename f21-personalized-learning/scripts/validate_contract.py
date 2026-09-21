import json
from pathlib import Path
from jsonschema import Draft202012Validator,FormatChecker
from openapi_spec_validator import validate
ROOT=Path(__file__).resolve().parents[1]
spec=json.loads((ROOT/"docs/openapi.json").read_text())
validate(spec)
def checker(schema):
    return Draft202012Validator({**schema,'components':spec['components']},format_checker=FormatChecker())
count=0
for fixture in json.loads((ROOT/"tests/fixtures/api-examples.json").read_text()):
    checker(spec["components"]["schemas"][fixture["schema"]]).validate(fixture["value"])
    count+=1
for path in spec["paths"].values():
    for op in path.values():
        if "requestBody" in op:
            body=op["requestBody"]["content"]["application/json"]
            checker(body["schema"]).validate(body["example"])
        for response in op.get('responses',{}).values():
            media=response.get('content',{}).get('application/json',{})
            if 'example' in media:checker(media['schema']).validate(media['example'])
            for example in media.get('examples',{}).values():
                checker(media['schema']).validate(example['value'])
print(f"OpenAPI valid; {count} response examples and request examples valid")

## Activate the virtual environment
```bash
source .venv/bin/activate
````

## Run the environment check

```bash
python scripts/check_env.py
```

## Expected result

You should see:

* PASS for Python version
* PASS for `grcc` on PATH
* PASS for GNU Radio import/version

If all checks pass, the local development environment is ready.

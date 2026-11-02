# Test package for the pipeline.
#
# This file exists so pytest imports the tests as `pipeline.tests.*` and walks
# up past `pipeline/__init__.py` to put the REPO ROOT on sys.path — which is
# what the modules under test need, since they import each other absolutely
# (`from pipeline.port import ...`). Without it the tests would import as
# top-level modules and `import pipeline.*` would fail.

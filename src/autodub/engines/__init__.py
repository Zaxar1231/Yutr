"""Heavy model engines.

Torch-based engines run in a separate process (``worker.py``) inside the engine Python
environment. Modules in this package must import only the standard library at module level:
the worker environment does not have the app's dependencies.
"""

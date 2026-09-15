# samples/

Put real Workday or OneUSG exports here when you want the importer tuned to their exact layout.
Everything in this folder except this README is git-ignored. Don't commit real exports: they contain financial and personnel data.

Run `uv run python manage.py seed_demo` to write synthetic example files to `samples/demo/`.

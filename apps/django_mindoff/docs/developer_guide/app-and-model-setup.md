# App and Model Setup

Create your apps and models using the CLI so routing, serializers, and conventions stay consistent from day one.

## Prerequisites

- [Django Mindoff Project](installation-configuration.md).
- Work from the project root (the directory that contains `manage.py` and `mindoff.py`) and [activate the virtual environment](installation-configuration.md#4-run-virtual-environment).

## Implementation

### 1. Create an App

Run:

```bash
python mindoff.py create
```

<div class="admonition warning">
<p class="admonition-title">Use the CLI for scaffolding</p>
<p>Create apps, models, and APIs through <code>python mindoff.py create</code> only.</p>
<p>Manual file creation can skip required registration and route wiring, which is critical for <code>django-mindoff</code> to function as intended.</p>
</div>

In the interactive menu, select:

- `1` for `app`

Then provide app names in snake_case (space-separated if creating multiple), for example:

```text
orders
```

After creation, you should see:

- App directory: `apps/orders/`
- App module registered in `INSTALLED_APPS`
- App-level URL wiring in place for versioned routing
- If creating multiple apps, enter names space-separated in one run.

### 2. Create Models

Run the same command again:

```bash
python mindoff.py create
```

In the interactive menu, select:

- `3` for `model`

Then:

1. Select the target app from the list.
2. Enter model names in PascalCase (space-separated), for example:

```text
Order OrderItem
```

**What the CLI Generates for Each Model**

`python mindoff.py create` (option `3`) makes these changes for each model:

1. The CLI normalizes given model names to PascalCase and adds `Model` suffix automatically when needed.
2. Appends a new model class to `apps/<app_name>/models.py`.
3. Sets the model base class to `mindoff_models.TimeStampModel`.
4. Adds an `id` field as:
   `models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, db_column="id")`.
5. Adds a managed marker line for where model fields should be inserted.
6. Adds `Meta.db_table` using `tbl_<app_name>_<model_base_name>`.
7. Adds `__str__` returning `str(self.id)`.
8. Appends a matching serializer class in `apps/<app_name>/serializers.py` with `fields = "__all__"`.
9. Skips duplicate creation if the class already exists.

**What `TimeStampModel` Adds**

- Every generated model gets standard creation/update timestamps by default.
- Timestamp fields are managed automatically by Django (no manual assignment needed).
- Consistent timestamp availability helps auditing, sorting, and debugging across models.

`mindoff_models.TimeStampModel` is an abstract base model that contributes:

```python
created_at = models.DateTimeField(auto_now_add=True)
updated_at = models.DateTimeField(auto_now=True)
```

<div class="admonition info">
<p class="admonition-title">Important: UUIDv4 primary key support</p>
<p><code>django-mindoff</code> expects UUID primary keys generated with <code>uuid.uuid4</code> for model workflows. Keep model primary keys as UUIDv4 to stay compatible with framework validation and CRUD pipelines.</p>
<p>Django User Model is also automatically configured to use a UUID primary key so identity handling stays consistent across model and relation workflows.</p>
</div>

<div class="admonition warning">
<p class="admonition-title">Keep primary key name and DB column as <code>id</code></p>
<p>Keep the model primary key field name as <code>id</code> and the database column as <code>db_column="id"</code>. Renaming the key or DB column can break assumptions used by <code>django-mindoff</code> CRUD and validation flows.</p>
</div>

After model creation, run migrations before using the model in code.

### 3. Create Foreign Key Field

Run the same command again:

```bash
python mindoff.py create
```

In the interactive menu, select:

- `4` for `foreign-key`

<div class="admonition tip">
<p class="admonition-title">Optional Foreign Key Creation</p>
<p>Foreign-key creation is optional in this flow. The main advantage is that django-mindoff automatically handles the cross-reference imports, naming conventions of the relations, and reverse keys for an enhanced developer experience.</p>
</div>

When prompted:

1. Select an existing model.
2. Enter relationship details.

### 4. Run Migrations

If models were added or changed:

```bash
python manage.py makemigrations
python manage.py migrate
```

## Core Concepts

### 1. Scaffolding Structure

After scaffolding completes, `django-mindoff` applies the project structure and registrations automatically.
The generated output includes:

1. `apps/<app_name>/` with standard app files such as `models.py`, `serializers.py`, `tests/`, `apis/`, etc.,
2. App registration in `config/settings.py` under `INSTALLED_APPS`.
3. Route and module wiring needed for imports and migrations.

For full structure details, see [Management Kit](../architecture/management-kit.md).

### 2. Practical Conventions

- Keep one domain per app (`users`, `billing`, `orders`) for easier project management.
- Prefer singular model names (`Order`, `Payment`, `CustomerProfile`).
- Keep model fields grouped under the managed marker to avoid merge conflicts.

## Troubleshooting

- `mindoff.py` command fails  
  Run the command from the project root and ensure the virtual environment is active.
- App created but not importable  
  Confirm the app folder name is a valid Python module name and that it is listed in `INSTALLED_APPS`.

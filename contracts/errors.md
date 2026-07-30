# Error contract

HTTP APIs use `application/problem+json` following RFC 9457. Every error has:

```json
{
  "type": "https://docs.storecipe.example/problems/validation-error",
  "title": "Request validation failed",
  "status": 422,
  "detail": "One or more fields are invalid.",
  "instance": "/v1/recipes",
  "request_id": "01J...",
  "errors": [{"field": "title", "message": "Field is required"}]
}
```

`type`, `title`, `status`, and `request_id` are always present. `detail`, `instance`,
and `errors` are optional. Internal exceptions are logged with the same request ID
but never returned to clients.

## Duplicate URL import conflicts

`POST /v1/imports/url` returns `409 Conflict` with one of the following RFC 9457
problem details when the source URL already exists for the authenticated user.

### Active import job

An active URL import is never bypassed, including when the request uses
`duplicatePolicy: allow`.

```json
{
  "type": "https://docs.storecipe.example/problems/active-url-import-exists",
  "title": "An active import already exists for this URL",
  "status": 409,
  "detail": "Wait for the existing import to finish or cancel it before starting another.",
  "instance": "/v1/imports/url",
  "request_id": "01J...",
  "errorCategory": "active_url_import_exists",
  "existingJobId": "5aac13b6-08f1-48fa-852f-fb1e2f7daf52"
}
```

### Saved recipe source

With the default `duplicatePolicy: warn`, a source URL already saved in Catalog is
reported as a conflict. Clients may resubmit with `duplicatePolicy: allow` after
informing the user; that policy does not override the active-import rule above.

```json
{
  "type": "https://docs.storecipe.example/problems/recipe-source-exists",
  "title": "A recipe already exists for this URL",
  "status": 409,
  "detail": "The source URL is already associated with a saved recipe.",
  "instance": "/v1/imports/url",
  "request_id": "01J...",
  "errorCategory": "recipe_source_exists",
  "existingRecipeId": "31c2bc28-6a35-4b14-b0b2-10e75e4b2446"
}
```

If the default Catalog source lookup is unavailable, `POST /v1/imports/url`
returns `503 Service Unavailable` rather than accepting a request without the
duplicate check.

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


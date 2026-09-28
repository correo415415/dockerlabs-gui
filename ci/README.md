# CI

`ci.yml` es el workflow de GitHub Actions (ruff + pytest en ubuntu/windows/macos).
El token del asistente no tiene permiso `workflows`, por lo que hay que activarlo a mano:

```bash
mkdir -p .github/workflows && git mv ci/ci.yml .github/workflows/ci.yml
git commit -m "ci: activar GitHub Actions" && git push
```

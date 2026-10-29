## Qué cambia

<!-- Qué cambia para quien usa el proyecto y por qué. Si es un fix o hotfix: cuál era el error y cómo se reproducía. -->

## Tipo de rama

- [ ] `feature/*` → `develop`: algo nuevo
- [ ] `fix/*` → `develop`: error no urgente
- [ ] `release/X.Y.Z` → `main`: cierre de versión, sin funcionalidades nuevas
- [ ] `hotfix/X.Y.Z` → `main`: error urgente en producción
- [ ] `backport/X.Y.Z` → `develop`: trae `main` a `develop` después de una release o hotfix

## Checklist

- [ ] `ruff check .` y `python -m pytest -q` pasan en local
- [ ] Si es un fix o hotfix, hay un test que reproduce el error (falla sin el cambio)
- [ ] Agregué una línea en `CHANGELOG.md`, dentro de `[Sin publicar]` (los backports no llevan)
- [ ] Si cambia el comportamiento en producción, lo dice el changelog, con lo que haya que migrar al desplegar
- [ ] Si es un hotfix, después del merge va el `backport/X.Y.Z` a `develop`
- [ ] No incluye `.env`, credenciales ni datos de clientes

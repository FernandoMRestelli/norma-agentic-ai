#!/usr/bin/env bash
# Rechaza una PR que no respeta el flujo de ramas o que no anota su cambio en CHANGELOG.md.
# Las reglas están explicadas en CONTRIBUTING.md; si cambian, se cambian en los dos lados.
#
# En local (compara contra origin/<BASE_REF>, así que conviene hacer `git fetch` antes):
#   HEAD_REF=feature/x BASE_REF=develop bash .github/scripts/verificar_reglas_pr.sh

set -euo pipefail

: "${HEAD_REF:?falta HEAD_REF (rama de la PR)}"
: "${BASE_REF:?falta BASE_REF (rama destino)}"

VERSION_RE='[0-9]+\.[0-9]+\.[0-9]+'
errores=0

error() {
    echo "::error title=$1::$2"
    errores=$((errores + 1))
}

case "$BASE_REF" in
    develop)
        if [[ ! "$HEAD_REF" =~ ^(feature|fix)/[a-z0-9]+(-[a-z0-9]+)*$ ]] \
            && [[ ! "$HEAD_REF" =~ ^backport/${VERSION_RE}$ ]] \
            && [[ "$HEAD_REF" != "main" ]]; then
            error "Nombre de rama inválido" \
                "A develop solo entran feature/<nombre>, fix/<nombre> (minúsculas, números y guiones) o backport/X.Y.Z. La rama es '$HEAD_REF'."
        fi
        ;;
    main)
        if [[ "$HEAD_REF" =~ ^(release|hotfix)/(${VERSION_RE})$ ]]; then
            version="${BASH_REMATCH[2]}"
            # Escapa los puntos: si no, "## [1.2.3]" también aceptaría "## [1x2x3]"
            if ! grep -qE "^## \[${version//./\\.}\]" CHANGELOG.md; then
                error "Falta la versión en el changelog" \
                    "CHANGELOG.md no tiene una sección '## [$version]'. Pasá [Sin publicar] a [$version] - AAAA-MM-DD."
            fi
        else
            error "Nombre de rama inválido" \
                "A main solo entran release/X.Y.Z o hotfix/X.Y.Z. La rama es '$HEAD_REF'."
        fi
        ;;
    *)
        error "Rama destino no soportada" "Las PR van a develop o a main, no a '$BASE_REF'."
        ;;
esac

# Un backport solo trae a develop lo que ya está anotado en main, así que no lleva entrada propia
if [[ ! "$HEAD_REF" =~ ^backport/ ]] && [[ "$HEAD_REF" != "main" ]]; then
    if ! git rev-parse --verify --quiet "origin/$BASE_REF" > /dev/null; then
        error "Falta la rama base" "No existe origin/$BASE_REF para comparar. Corré 'git fetch origin $BASE_REF'."
    elif ! git diff --name-only "origin/$BASE_REF...HEAD" | grep -qx 'CHANGELOG.md'; then
        error "Falta la entrada en el changelog" \
            "Agregá una línea en CHANGELOG.md, dentro de [Sin publicar], que cuente el cambio para quien usa el proyecto."
    fi
fi

if (( errores > 0 )); then
    exit 1
fi
echo "Reglas de PR: OK ($HEAD_REF -> $BASE_REF)"

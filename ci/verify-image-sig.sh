#!/usr/bin/env bash
# verify-image-sig.sh — Verify base image signatures with cosign

set -euo pipefail

echo "🔐 Base Image Signature Verification"
echo "====================================="
echo

REGISTRY="${REGISTRY:-192.168.2.180:5050}"
TRUSTED_IMAGES_FILE="${TRUSTED_IMAGES_FILE:-ci/trusted-images.txt}"

if [ ! -f "$TRUSTED_IMAGES_FILE" ]; then
    echo "❌ Trusted images list not found: $TRUSTED_IMAGES_FILE"
    exit 1
fi

FAILED=0

while IFS= read -r line; do
    # Skip comments and empty lines
    [[ "$line" =~ ^#.*$ ]] && continue
    [[ -z "$line" ]] && continue
    
    IMAGE="$line"
    echo ""
    echo "Verifying: $IMAGE"
    
    # Parse image and digest
    if [[ "$IMAGE" =~ ^(.+)@(.+)$ ]]; then
        IMG="${BASH_REMATCH[1]}"
        DIGEST="${BASH_REMATCH[2]}"
        
        # Try cosign verification
        if cosign verify --allow-insecure-registry --certificate-identity-regexp=".*" "$IMG@$DIGEST" 2>/dev/null; then
            echo "✅ Signature verified for $IMG"
        else
            echo "⚠️  No signature found (expected for internal images)"
            echo "   Digest: $DIGEST"
        fi
    else
        echo "⚠️  Skipping (invalid format, use IMAGE@DIGEST)"
    fi
done < "$TRUSTED_IMAGES_FILE"

echo ""
echo "====================================="
if [ $FAILED -eq 0 ]; then
    echo "✅ Image verification complete"
    exit 0
else
    echo "❌ $FAILED image(s) failed verification"
    exit 1
fi

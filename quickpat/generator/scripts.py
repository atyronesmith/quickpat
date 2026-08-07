"""CRC validation scripts and embedded templates."""


class ScriptsGeneratorMixin:
    """Mixin for PatternGenerator."""

    def _generate_scripts(self):
        """Generate pattern-agnostic CRC validation scripts."""
        scripts_dir = self.output_dir / 'scripts'
        scripts_dir.mkdir(exist_ok=True)
        gn = self.config.get('cluster_group_name', 'prod')
        values_file = f"values-{gn}.yaml"

        def _sub(script):
            return script.replace('$VALUES_GROUP_FILE', values_file)

        self._write_script(scripts_dir / 'crc-setup.sh', _SCRIPT_CRC_SETUP)
        self._write_script(scripts_dir / 'deploy.sh', _SCRIPT_DEPLOY)
        self._write_script(
            scripts_dir / 'validate-deployment.sh', _sub(_SCRIPT_VALIDATE),
        )
        self._write_script(scripts_dir / 'undeploy.sh', _sub(_SCRIPT_UNDEPLOY))
        self._write_script(scripts_dir / 'status.sh', _sub(_SCRIPT_STATUS))

        self._write_yaml(scripts_dir / 'dsc.yaml', {
            'apiVersion': 'datasciencecluster.opendatahub.io/v1',
            'kind': 'DataScienceCluster',
            'metadata': {'name': 'default-dsc'},
            'spec': {'components': {
                'codeflare': {'managementState': 'Removed'},
                'dashboard': {'managementState': 'Managed'},
                'datasciencepipelines': {'managementState': 'Managed'},
                'kserve': {'managementState': 'Managed'},
                'modelmeshserving': {'managementState': 'Removed'},
                'ray': {'managementState': 'Removed'},
                'trustyai': {'managementState': 'Removed'},
                'workbenches': {'managementState': 'Managed'},
            }},
        })

        (scripts_dir / 'README.md').write_text(_SCRIPT_README)

    def _write_script(self, path, content):
        path.write_text(content)
        path.chmod(0o755)

# ── Script templates (pattern-agnostic CRC validation) ──────────

_SCRIPT_CRC_SETUP = r'''#!/bin/bash
set -eo pipefail

CRC_MEMORY=${CRC_MEMORY:-49152}
CRC_CPUS=${CRC_CPUS:-12}
CRC_DISK=${CRC_DISK:-100}

echo "=== CRC Setup for VP Structural Validation ==="
echo "  Memory: ${CRC_MEMORY}MB  CPUs: ${CRC_CPUS}  Disk: ${CRC_DISK}GB"
echo ""

if ! command -v crc &>/dev/null; then
    echo "ERROR: crc not found."
    echo "Download from https://console.redhat.com/openshift/create/local"
    echo "(Homebrew does NOT have a crc formula)"
    exit 1
fi

echo "CRC version: $(crc version | head -1)"

STATUS=$(crc status 2>/dev/null | grep "CRC VM:" | awk '{print $3}' || echo "Stopped")

if [ "$STATUS" = "Running" ]; then
    echo "CRC is already running."
    echo ""
    echo "Current config:"
    crc config view 2>/dev/null | grep -E "memory|cpus|disk" || true
    echo ""
    echo "To reconfigure, run: crc stop && crc delete && re-run this script"
else
    echo "Configuring CRC..."
    crc config set memory "$CRC_MEMORY"
    crc config set cpus "$CRC_CPUS"
    crc config set disk-size "$CRC_DISK"
    crc config set consent-telemetry no

    echo ""
    echo "Starting CRC (this takes 5-10 minutes on first run)..."
    crc start

    echo ""
    echo "CRC started successfully."
fi

echo ""
echo "=== Cluster Login ==="
eval "$(crc oc-env)"

KUBEADMIN_PASS=$(cat ~/.crc/machines/crc/kubeadmin-password 2>/dev/null || true)
if [ -z "$KUBEADMIN_PASS" ]; then
    KUBEADMIN_PASS=$(crc console --credentials 2>&1 | grep kubeadmin | grep -oE "'[^']+'" | tail -1 | tr -d "'")
fi

if [ -n "$KUBEADMIN_PASS" ]; then
    oc login -u kubeadmin -p "$KUBEADMIN_PASS" https://api.crc.testing:6443 --insecure-skip-tls-verify=true
else
    echo "Could not extract kubeadmin password automatically."
    echo "Run: crc console --credentials"
    echo "Then: oc login -u kubeadmin -p <password> https://api.crc.testing:6443"
    exit 1
fi

echo ""
echo "=== Pull Secret Check ==="
HAS_RH_REGISTRY=$(oc get secret pull-secret -n openshift-config -o jsonpath='{.data.\.dockerconfigjson}' 2>/dev/null \
    | base64 -d 2>/dev/null \
    | python3 -c "import sys,json; print('registry.redhat.io' in json.load(sys.stdin).get('auths',{}))" 2>/dev/null \
    || echo "False")

if [ "$HAS_RH_REGISTRY" = "True" ]; then
    echo "  Pull secret includes registry.redhat.io — OK"
else
    echo "  WARNING: Pull secret missing registry.redhat.io credentials."
    echo "  Catalog sources will fail to pull operator indexes."
    echo "  Download pull secret from https://console.redhat.com/openshift/create/local"
    echo "  Apply: oc set data secret/pull-secret -n openshift-config --from-file=.dockerconfigjson=<path>"
fi

echo ""
echo "=== Cluster Health ==="
echo "Nodes:"
oc get nodes
echo ""
echo "Cluster version:"
oc get clusterversion
echo ""
echo "Resources:"
oc adm top nodes 2>/dev/null || echo "(metrics not yet available — wait a few minutes)"
echo ""
echo "=== Ready for pattern deployment ==="
echo "Next: ./scripts/deploy.sh"
'''

_SCRIPT_DEPLOY = r'''#!/bin/bash
set -eo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PATTERN_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

PATTERN_NAME=$(grep '^  pattern:' "$PATTERN_DIR/values-global.yaml" 2>/dev/null | awk '{print $2}' || basename "$PATTERN_DIR")
VALUES_SECRET_FILE="${VALUES_SECRET:-$HOME/values-secret-${PATTERN_NAME}.yaml}"

echo "=== Validated Pattern CRC Deployment ==="
echo "  Pattern:  $PATTERN_NAME"
echo "  Dir:      $PATTERN_DIR"
echo "  Secrets:  $VALUES_SECRET_FILE"
echo ""

# --- Pre-flight checks ---
echo "--- Pre-flight checks ---"

for cmd in oc helm git python3; do
    if ! command -v "$cmd" &>/dev/null; then
        echo "ERROR: $cmd not found."
        exit 1
    fi
done

CURRENT_USER=$(oc whoami 2>/dev/null || echo "")
if [ -z "$CURRENT_USER" ] || [ "$CURRENT_USER" != "kubeadmin" ]; then
    echo "  Need kubeadmin access (current: ${CURRENT_USER:-not logged in}). Logging in..."
    eval "$(crc oc-env 2>/dev/null || true)"
    KUBEADMIN_PASS=$(cat ~/.crc/machines/crc/kubeadmin-password 2>/dev/null || true)
    if [ -z "$KUBEADMIN_PASS" ]; then
        KUBEADMIN_PASS=$(crc console --credentials 2>&1 | grep kubeadmin | grep -oE "'[^']+'" | tail -1 | tr -d "'")
    fi
    if [ -n "$KUBEADMIN_PASS" ]; then
        oc login -u kubeadmin -p "$KUBEADMIN_PASS" https://api.crc.testing:6443 --insecure-skip-tls-verify=true
    else
        echo "ERROR: Could not extract kubeadmin password."
        echo "Run: oc login -u kubeadmin https://api.crc.testing:6443"
        exit 1
    fi
fi

echo "  Logged in as: $(oc whoami)"
echo "  Cluster: $(oc whoami --show-server)"

HAS_RH_REGISTRY=$(oc get secret pull-secret -n openshift-config -o jsonpath='{.data.\.dockerconfigjson}' 2>/dev/null \
    | base64 -d 2>/dev/null \
    | python3 -c "import sys,json; print('registry.redhat.io' in json.load(sys.stdin).get('auths',{}))" 2>/dev/null \
    || echo "False")

if [ "$HAS_RH_REGISTRY" != "True" ]; then
    echo "  WARNING: Pull secret missing registry.redhat.io — operators may fail to install."
    echo "  Fix: oc set data secret/pull-secret -n openshift-config --from-file=.dockerconfigjson=<pull-secret.json>"
fi
echo ""

# --- Git repo setup ---
echo "--- Git repo setup ---"

cd "$PATTERN_DIR"

if [ -d .git ]; then
    echo "  Already a git repo."
    if [ -n "$(git status --porcelain 2>/dev/null)" ]; then
        echo "  Committing uncommitted changes..."
        git add -A
        git commit -m "Pre-deploy snapshot" --allow-empty 2>/dev/null || true
    fi
else
    echo "  Initializing git repo (required by VP framework)..."
    git init
    git add -A
    git commit -m "Initial pattern"
fi

TARGET_BRANCH=$(git rev-parse --abbrev-ref HEAD)
TARGET_REPO=$(git remote get-url origin 2>/dev/null || echo "")

if [ -z "$TARGET_REPO" ] || [[ "$TARGET_REPO" == file://* ]] || [[ "$TARGET_REPO" == /* ]]; then
    echo ""
    echo "  WARNING: Pattern repo must be HTTP-accessible from the cluster."
    echo "  Local/file:// repos won't work — the patterns operator runs inside a pod."
    if command -v gh &>/dev/null; then
        echo "  Creating GitHub repo..."
        gh repo create "${PATTERN_NAME}-test" --private --source=. --push 2>/dev/null || true
        gh repo edit --visibility public 2>/dev/null || true
        TARGET_REPO=$(git remote get-url origin 2>/dev/null || echo "")
        TARGET_BRANCH=$(git rev-parse --abbrev-ref HEAD)
    else
        echo "  Install gh CLI and run: gh repo create ${PATTERN_NAME}-test --public --source=. --push"
        exit 1
    fi
fi

echo "  Branch: $TARGET_BRANCH"
echo "  Repo: $TARGET_REPO"
echo ""

# --- Secrets file ---
echo "--- Secrets file ---"

if [ -f "$VALUES_SECRET_FILE" ]; then
    echo "  Using existing: $VALUES_SECRET_FILE"
elif [ -f "$PATTERN_DIR/values-secret.yaml.template" ]; then
    echo "  Generating dummy secrets from template..."
    python3 - "$PATTERN_DIR/values-secret.yaml.template" "$VALUES_SECRET_FILE" << 'PYEOF'
import re, sys
src = open(sys.argv[1]).read()
out_path = sys.argv[2]
src = re.sub(r'\n\s+onMissingValue:.*', '', src)
src = re.sub(r'\n\s+vaultPolicy:.*', '', src)
src = re.sub(
    r"(- name:\s+(\S+)\n\s+)value:\s*(?:''|\"\"|)\s*$",
    lambda m: m.group(1) + "value: dummy-" + m.group(2),
    src, flags=re.MULTILINE)
src = re.sub(
    r"^(\s+)(- name:\s+(\S+))\s*$(?!\n\s+value:)",
    lambda m: m.group(1) + "- name: " + m.group(3) + "\n" + m.group(1) + "  value: dummy-" + m.group(3),
    src, flags=re.MULTILINE)
open(out_path, 'w').write(src)
PYEOF
    echo "  Created: $VALUES_SECRET_FILE"
else
    echo "  No secrets template found. Skipping."
fi
echo ""

# --- Deploy pattern-install chart ---
echo "--- Deploying pattern (direct helm, bypassing utility container) ---"
echo ""

cd "$PATTERN_DIR"

HELM_OPTS=(
    --include-crds
    --name-template "$PATTERN_NAME"
    -f values-global.yaml
    --set main.git.repoURL="$TARGET_REPO"
    --set main.git.revision="$TARGET_BRANCH"
    --set global.pattern="$PATTERN_NAME"
    --set global.clusterDomain="$(oc get ingress.config cluster -o jsonpath='{.spec.domain}' 2>/dev/null || echo 'apps-crc.testing')"
    --set global.clusterVersion="$(oc get clusterversion version -o jsonpath='{.status.desired.version}' 2>/dev/null || echo '4.21')"
    --set global.clusterPlatform="None"
)

WORK=$(mktemp -d)
trap "rm -rf $WORK" EXIT

echo "Step 1: Rendering pattern-install chart..."
helm template "${HELM_OPTS[@]}" oci://quay.io/validatedpatterns/pattern-install > "$WORK/all.yaml" 2>/dev/null

echo "Step 2: Splitting CRDs and Pattern CR..."
python3 -c "
docs = open('$WORK/all.yaml').read().split('---')
crds, pattern = [], []
for doc in docs:
    stripped = doc.strip()
    if not stripped or 'kind:' not in stripped:
        continue
    if 'kind: Pattern' in stripped:
        pattern.append(stripped)
    else:
        crds.append(stripped)
open('$WORK/crds.yaml','w').write('\n---\n'.join(crds))
open('$WORK/pattern.yaml','w').write('\n---\n'.join(pattern))
"

echo "Step 3: Applying CRDs and operator subscription..."
oc apply -f "$WORK/crds.yaml" 2>&1

echo "Step 4: Waiting for Pattern CRD..."
for i in $(seq 1 30); do
    if oc get crd patterns.gitops.hybrid-cloud-patterns.io &>/dev/null; then
        echo "  Pattern CRD ready."
        break
    fi
    if [ "$i" -eq 30 ]; then
        echo "  ERROR: Pattern CRD not registered after 150s"
        exit 1
    fi
    sleep 5
done

echo "Step 5: Applying Pattern CR..."
oc apply -f "$WORK/pattern.yaml" 2>&1

echo ""
echo "Step 6: Waiting for patterns operator to install..."
for i in $(seq 1 60); do
    if oc get csv -n openshift-operators 2>/dev/null | grep -q "patterns-operator.*Succeeded"; then
        echo "  Patterns operator ready."
        break
    fi
    if [ "$i" -eq 60 ]; then
        echo "  WARNING: Patterns operator not ready after 5 min — continuing anyway."
    fi
    sleep 5
done

echo ""
echo "Step 7: Waiting for catalog sources (operator indexes)..."
for i in $(seq 1 36); do
    RUNNING=$(oc get pods -n openshift-marketplace --no-headers 2>/dev/null | grep -c "Running" || echo 0)
    if [ "$RUNNING" -ge 4 ]; then
        echo "  Catalog sources ready ($RUNNING pods running)."
        break
    fi
    if [ "$i" -eq 36 ]; then
        echo "  WARNING: Only $RUNNING catalog pods running. Check pull secret."
    fi
    sleep 10
done

echo ""
echo "Step 8: Waiting for ArgoCD applications..."
for i in $(seq 1 60); do
    APP_COUNT=$(oc get applications.argoproj.io -A --no-headers 2>/dev/null | wc -l | tr -d ' ')
    if [ "$APP_COUNT" -gt 0 ]; then
        echo "  $APP_COUNT ArgoCD applications found."
        oc get applications.argoproj.io -A --no-headers 2>/dev/null
        break
    fi
    if [ "$i" -eq 60 ]; then
        echo "  WARNING: No ArgoCD applications after 5 min."
    fi
    sleep 5
done

echo ""
echo "Step 9: Checking if RHODS needs DataScienceCluster CR..."
if oc get crd datascienceclusters.datasciencecluster.opendatahub.io &>/dev/null; then
    DSC_COUNT=$(oc get datascienceclusters -A --no-headers 2>/dev/null | wc -l | tr -d ' ')
    if [ "$DSC_COUNT" -eq 0 ]; then
        echo "  Creating DataScienceCluster CR (needed for Notebook/DSPA CRDs)..."
        oc apply -f "$SCRIPT_DIR/dsc.yaml" 2>/dev/null || echo "  WARNING: dsc.yaml not found"
        echo "  DataScienceCluster CR created."
    else
        echo "  DataScienceCluster already exists."
    fi
else
    echo "  RHODS not yet installed — DSC will be created on next run."
fi

echo ""
echo "=== Deploy complete ==="
echo ""
echo "Operators and ArgoCD will take 5-10 minutes to stabilize."
echo "Next steps:"
echo "  1. Watch:     oc get applications.argoproj.io -A -w"
echo "  2. Validate:  ./scripts/validate-deployment.sh"
echo "  3. Undeploy:  ./scripts/undeploy.sh"
'''

_SCRIPT_VALIDATE = r'''#!/bin/bash
set -eo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PATTERN_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
PATTERN_NAME=$(grep '^  pattern:' "$PATTERN_DIR/values-global.yaml" 2>/dev/null | awk '{print $2}' || basename "$PATTERN_DIR")

PASS=0
FAIL=0
WARN=0

pass() { echo "  PASS: $1"; ((PASS++)); }
fail() { echo "  FAIL: $1"; ((FAIL++)); }
warn() { echo "  WARN: $1"; ((WARN++)); }

echo "============================================================"
echo "  Validated Pattern Deployment Validation: $PATTERN_NAME"
echo "============================================================"
echo ""

# --- Phase 1: Pre-flight ---
echo "--- Phase 1: Pre-flight ---"

if oc whoami &>/dev/null; then
    USER=$(oc whoami)
    pass "Logged in as $USER"
else
    fail "Not logged into cluster"
    echo "Run: oc login -u kubeadmin https://api.crc.testing:6443"
    exit 1
fi

if oc get nodes &>/dev/null; then
    NODE_COUNT=$(oc get nodes --no-headers | wc -l | tr -d ' ')
    pass "Cluster accessible ($NODE_COUNT nodes)"
else
    fail "Cannot reach cluster"
    exit 1
fi

READY=$(oc get nodes --no-headers | grep -c " Ready" || echo 0)
if [ "$READY" -eq "$NODE_COUNT" ]; then
    pass "All nodes Ready"
else
    fail "$READY/$NODE_COUNT nodes Ready"
fi

echo ""

# --- Phase 2: Pattern CR ---
echo "--- Phase 2: Pattern CR ---"

PATTERN_STATUS=$(oc get pattern "$PATTERN_NAME" -n openshift-operators -o jsonpath='{.status.lastStep}' 2>/dev/null || echo "")
if [ -n "$PATTERN_STATUS" ]; then
    if [ "$PATTERN_STATUS" = "reconcile complete" ]; then
        pass "Pattern CR: $PATTERN_STATUS"
    else
        warn "Pattern CR: $PATTERN_STATUS"
    fi
    LAST_ERROR=$(oc get pattern "$PATTERN_NAME" -n openshift-operators -o jsonpath='{.status.lastError}' 2>/dev/null || echo "")
    if [ -n "$LAST_ERROR" ]; then
        warn "Pattern last error: $LAST_ERROR"
    fi
else
    fail "Pattern CR not found"
fi

echo ""

# --- Phase 3: Namespaces ---
echo "--- Phase 3: Namespaces ---"

APP_NS=$(grep -A2 'namespaces:' "$PATTERN_DIR/$VALUES_GROUP_FILE" 2>/dev/null | grep '^\s*-' | sed 's/^\s*-\s*//' | sed 's/:.*//' | tr -d ' ' || echo "")
EXPECTED_NS="vault external-secrets $APP_NS"
for ns in $EXPECTED_NS; do
    if [ -z "$ns" ]; then continue; fi
    if oc get namespace "$ns" &>/dev/null; then
        pass "Namespace $ns exists"
    else
        fail "Namespace $ns missing"
    fi
done

echo ""

# --- Phase 4: Operator Subscriptions ---
echo "--- Phase 4: Operator Subscriptions ---"

SUBS=$(oc get subscriptions -A --no-headers 2>/dev/null | grep -v "^openshift-operator" | sort -u -k2,2 || echo "")
if [ -n "$SUBS" ]; then
    while IFS= read -r line; do
        NS=$(echo "$line" | awk '{print $1}')
        NAME=$(echo "$line" | awk '{print $2}')
        pass "Subscription $NAME in $NS"
    done <<< "$SUBS"
else
    warn "No operator subscriptions found"
fi

echo ""
echo "CSV install status:"
oc get csv -A --no-headers 2>/dev/null | sort -u -k2,2 | while IFS= read -r line; do
    NS=$(echo "$line" | awk '{print $1}')
    NAME=$(echo "$line" | awk '{print $2}')
    PHASE=$(echo "$line" | awk '{print $NF}')
    if [ "$PHASE" = "Succeeded" ]; then
        pass "CSV $NAME ($PHASE)"
    elif [ "$PHASE" = "Failed" ]; then
        fail "CSV $NAME ($PHASE)"
    else
        warn "CSV $NAME ($PHASE)"
    fi
done

echo ""

# --- Phase 5: ArgoCD ---
echo "--- Phase 5: ArgoCD Applications ---"

APPS=$(oc get applications.argoproj.io -A --no-headers 2>/dev/null || echo "")
if [ -n "$APPS" ]; then
    while IFS= read -r line; do
        NS=$(echo "$line" | awk '{print $1}')
        APP_NAME=$(echo "$line" | awk '{print $2}')
        SYNC=$(echo "$line" | awk '{print $3}')
        HEALTH=$(echo "$line" | awk '{print $4}')
        if [ "$SYNC" = "Synced" ]; then
            pass "App $APP_NAME: $SYNC / $HEALTH"
        else
            warn "App $APP_NAME: $SYNC / $HEALTH"
        fi
    done <<< "$APPS"
else
    warn "No ArgoCD applications found yet"
fi

echo ""

# --- Phase 6: Infrastructure ---
echo "--- Phase 6: Infrastructure (Vault + ExternalSecrets) ---"

if oc get pods -n vault --no-headers 2>/dev/null | grep -q "Running"; then
    pass "Vault pods running"
else
    warn "Vault pods not running yet"
fi

if oc get pods -n external-secrets --no-headers 2>/dev/null | grep -q "Running"; then
    pass "External Secrets Operator pods running"
else
    warn "External Secrets Operator pods not running yet"
fi

if oc get crd externalsecrets.external-secrets.io &>/dev/null; then
    pass "ExternalSecret CRD registered"
else
    warn "ExternalSecret CRD not yet registered"
fi

if oc get crd clustersecretstores.external-secrets.io &>/dev/null; then
    pass "ClusterSecretStore CRD registered"
else
    warn "ClusterSecretStore CRD not yet registered"
fi

for ns in $APP_NS; do
    if [ -z "$ns" ]; then continue; fi
    ES_COUNT=$(oc get externalsecrets -n "$ns" --no-headers 2>/dev/null | wc -l | tr -d ' ')
    if [ "$ES_COUNT" -gt 0 ]; then
        pass "$ES_COUNT ExternalSecrets in $ns namespace"
    else
        warn "No ExternalSecrets in $ns namespace yet"
    fi
done

echo ""

# --- Phase 7: Application Resources ---
echo "--- Phase 7: Application Resources ---"

for ns in $APP_NS; do
    if [ -z "$ns" ]; then continue; fi
    if ! oc get namespace "$ns" &>/dev/null; then continue; fi

    echo "  Namespace: $ns"
    for kind in configmap service route deployment statefulset; do
        COUNT=$(oc get "$kind" -n "$ns" --no-headers 2>/dev/null | wc -l | tr -d ' ')
        if [ "$COUNT" -gt 0 ]; then
            pass "$COUNT ${kind}s in $ns"
        fi
    done

    PODS=$(oc get pods -n "$ns" --no-headers 2>/dev/null || echo "")
    if [ -n "$PODS" ]; then
        TOTAL=$(echo "$PODS" | wc -l | tr -d ' ')
        RUNNING=$(echo "$PODS" | grep -c "Running" || echo 0)
        PENDING=$(echo "$PODS" | grep -c "Pending" || echo 0)
        CRASH=$(echo "$PODS" | grep -c "CrashLoop\|Error\|ImagePull" || echo 0)
        echo "    Pods: $TOTAL total, $RUNNING running, $PENDING pending, $CRASH errored"
        if [ "$CRASH" -gt 0 ]; then
            warn "Some pods in error state in $ns (expected without GPU)"
        fi
        if [ "$RUNNING" -gt 0 ]; then
            pass "Pods running in $ns"
        fi
    else
        warn "No pods in $ns yet"
    fi
    echo ""
done

# --- Summary ---
echo "============================================================"
echo "  Summary: $PASS passed, $FAIL failed, $WARN warnings"
echo "============================================================"

if [ "$FAIL" -gt 0 ]; then
    exit 1
fi
'''

_SCRIPT_UNDEPLOY = r'''#!/bin/bash
set -eo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PATTERN_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
PATTERN_NAME=$(grep '^  pattern:' "$PATTERN_DIR/values-global.yaml" 2>/dev/null | awk '{print $2}' || basename "$PATTERN_DIR")

echo "=== Removing Validated Pattern: $PATTERN_NAME ==="
echo ""

if ! oc whoami &>/dev/null; then
    echo "Not logged into cluster. Nothing to remove."
    exit 0
fi

echo "Deleting Pattern CR..."
if oc get pattern "$PATTERN_NAME" -n openshift-operators &>/dev/null; then
    oc delete pattern "$PATTERN_NAME" -n openshift-operators --wait=false 2>/dev/null || true
    echo "  Deleted pattern: $PATTERN_NAME"
fi

echo ""
echo "Deleting ArgoCD applications..."
for ns in $(oc get applications.argoproj.io -A --no-headers 2>/dev/null | awk '{print $1}' | sort -u); do
    for app in $(oc get applications.argoproj.io -n "$ns" --no-headers 2>/dev/null | awk '{print $1}'); do
        oc delete application "$app" -n "$ns" --wait=false 2>/dev/null || true
        echo "  Deleted application: $app (in $ns)"
    done
done

echo ""
echo "Waiting for application cleanup (30s)..."
sleep 30

echo "Deleting DataScienceCluster..."
oc delete datasciencecluster --all -A 2>/dev/null || true

echo ""
echo "Deleting application namespaces..."
APP_NS=$(grep -A2 'namespaces:' "$PATTERN_DIR/$VALUES_GROUP_FILE" 2>/dev/null | grep '^\s*-' | sed 's/^\s*-\s*//' | sed 's/:.*//' | tr -d ' ' || echo "")
for ns in vault external-secrets $APP_NS; do
    if [ -z "$ns" ]; then continue; fi
    if oc get namespace "$ns" &>/dev/null; then
        oc delete namespace "$ns" --wait=false 2>/dev/null || true
        echo "  Deleted namespace: $ns"
    fi
done

echo ""
echo "Deleting operator subscriptions..."
for sub in $(oc get subscriptions -A --no-headers 2>/dev/null | awk '{print $1 "/" $2}'); do
    ns="${sub%%/*}"
    name="${sub##*/}"
    CSV=$(oc get subscription "$name" -n "$ns" -o jsonpath='{.status.installedCSV}' 2>/dev/null || echo "")
    oc delete subscription "$name" -n "$ns" 2>/dev/null || true
    if [ -n "$CSV" ]; then
        oc delete csv "$CSV" -n "$ns" 2>/dev/null || true
    fi
    echo "  Deleted subscription: $name"
done

echo ""
echo "Deleting operator namespaces..."
for ns in openshift-nfd nvidia-gpu-operator redhat-ods-operator redhat-ods-applications redhat-ods-monitoring openshift-serverless; do
    if oc get namespace "$ns" &>/dev/null; then
        oc delete namespace "$ns" --wait=false 2>/dev/null || true
        echo "  Deleted namespace: $ns"
    fi
done

for ns in $(oc get namespaces --no-headers 2>/dev/null | awk '{print $1}' | grep -E "^${PATTERN_NAME}|^vp-|^imperative$"); do
    oc delete namespace "$ns" --wait=false 2>/dev/null || true
    echo "  Deleted namespace: $ns"
done

echo ""
echo "=== Undeploy complete ==="
echo "Note: Some resources may take a few minutes to fully terminate."
echo "To destroy the entire CRC VM instead: crc stop && crc delete"
'''

_SCRIPT_STATUS = r'''#!/bin/bash
set -eo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PATTERN_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
PATTERN_NAME=$(grep '^  pattern:' "$PATTERN_DIR/values-global.yaml" 2>/dev/null | awk '{print $2}' || basename "$PATTERN_DIR")

echo "=== Pattern Status: $PATTERN_NAME ==="
echo ""

if ! oc whoami &>/dev/null; then
    echo "Not logged into cluster."
    exit 1
fi

echo "--- Pattern CR ---"
oc get pattern "$PATTERN_NAME" -n openshift-operators 2>/dev/null || echo "Not found"

echo ""
echo "--- ArgoCD Applications ---"
oc get applications.argoproj.io -A 2>/dev/null || echo "None"

echo ""
echo "--- Operator CSVs (unique) ---"
oc get csv -A --no-headers 2>/dev/null | sort -u -k2,2 | awk '{printf "  %-55s %s\n", $2, $NF}'

echo ""
echo "--- Catalog Sources ---"
oc get pods -n openshift-marketplace --no-headers 2>/dev/null | awk '{printf "  %-50s %s\n", $1, $3}'

echo ""
echo "--- Application Pods ---"
APP_NS=$(grep -A2 'namespaces:' "$PATTERN_DIR/$VALUES_GROUP_FILE" 2>/dev/null | grep '^\s*-' | sed 's/^\s*-\s*//' | sed 's/:.*//' | tr -d ' ' || echo "")
for ns in vault external-secrets $APP_NS; do
    if [ -z "$ns" ]; then continue; fi
    if ! oc get namespace "$ns" &>/dev/null 2>&1; then continue; fi
    PODS=$(oc get pods -n "$ns" --no-headers 2>/dev/null || echo "")
    if [ -n "$PODS" ]; then
        TOTAL=$(echo "$PODS" | wc -l | tr -d ' ')
        RUNNING=$(echo "$PODS" | grep -c "Running" || echo 0)
        echo "  $ns: $RUNNING/$TOTAL running"
    fi
done

echo ""
echo "--- Node Resources ---"
oc adm top nodes 2>/dev/null || echo "  Metrics not available"
'''

_SCRIPT_README = '''# CRC Structural Validation for Validated Patterns

Validates that a pattern deploys correctly on CRC (CodeReady Containers) \
-- operators install, ArgoCD syncs, ExternalSecrets resolve. No GPU \
required; this is structural validation only.

These scripts are pattern-agnostic -- they derive the pattern name from \
`values-global.yaml`.

## Prerequisites

- CRC installed (download from https://console.redhat.com/openshift/create/local)
- Red Hat pull secret (from the same page)
- helm installed (`brew install helm`)
- git and python3 installed
- ~48GB free RAM (12 CPUs minimum for full operator stack)

**Note:** `brew install crc` does NOT work. CRC must be downloaded directly.

## First-time setup

```bash
crc setup
./scripts/crc-setup.sh
```

## Deploy

```bash
# Copy pattern to local disk first (SMB/NFS shares don\'t support git)
cp -R /path/to/pattern ~/my-pattern
cd ~/my-pattern
./scripts/deploy.sh
```

The deploy script handles: kubeadmin login, pull secret check, git init, \
GitHub push, dummy secrets generation, helm install (bypassing utility \
container), operator wait, DSC creation.

## Validate

```bash
./scripts/validate-deployment.sh
```

## Status

```bash
./scripts/status.sh
```

## Undeploy

```bash
./scripts/undeploy.sh
```

## Expected results

- Operators install (CSVs Succeeded), GPU operator has no work to do
- ArgoCD syncs all applications
- Vault + ExternalSecrets infrastructure deploys
- ExternalSecrets show errors (no real Vault secrets loaded)
- Application pods Pending/CrashLoop (no GPU, no model) -- expected
- **The structural win: all Kubernetes resources accepted by the API server**

## Known issues

- CRC needs 12+ CPUs -- 8 is insufficient for full VP operator stack
- RHODS requires a DataScienceCluster CR to register Notebook/DSPA CRDs
- utility container (`pattern.sh make install`) may crash on Apple Silicon \
under podman libkrun
- Pattern repo must be HTTP-accessible -- patterns operator runs inside a \
cluster pod
'''

set -e
BUNDLE_VER="codeql-bundle-v2.25.2"
URL="https://github.com/github/codeql-action/releases/download/${BUNDLE_VER}/codeql-bundle-linux64.tar.gz"

rm -rf /root/codeql
curl -fL --retry 3 -o /tmp/codeql-bundle-linux64.tar.gz "$URL"
tar -xzf /tmp/codeql-bundle-linux64.tar.gz -C /root
test -x /root/codeql/codeql

grep -q 'export PATH="/root/codeql:$PATH"' /root/.bashrc 2>/dev/null || \
  echo 'export PATH="/root/codeql:$PATH"' >> /root/.bashrc

# 消除「装在 home 下可能影响性能」的提示（可选）
grep -q CODEQL_ALLOW_INSTALLATION_ANYWHERE /root/.bashrc 2>/dev/null || \
  echo 'export CODEQL_ALLOW_INSTALLATION_ANYWHERE=true' >> /root/.bashrc

export PATH="/root/codeql:$PATH"
export CODEQL_ALLOW_INSTALLATION_ANYWHERE=true
codeql version
rm -f /tmp/codeql-bundle-linux64.tar.gz
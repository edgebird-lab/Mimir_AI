"""Live Zone W boot + isolation test (run on the HOST, needs /dev/kvm).

Proves: clone-gate filters secrets, VM boots with a RW workspace, exec/write/read/list/git work,
and the jail is isolated (no network, no /dev/kfd, rootfs read-only). Ephemeral — cleans up."""
import os
import shutil
import sys
import tempfile

from mimir.workspace_ctl import WorkspaceVM, build_workspace_disk

OK = "\033[32m[OK]\033[0m"
NO = "\033[31m[FAIL]\033[0m"
fails = 0


def check(cond, msg):
    global fails
    print(f"  {OK if cond else NO} {msg}")
    if not cond:
        fails += 1


def main():
    # --- build a fake source project with a normal file, a secret, a .env, and a .git ---
    src = tempfile.mkdtemp(prefix="mimir-src-")
    open(os.path.join(src, "app.py"), "w").write("def hi():\n    return 'hello'\n")
    os.makedirs(os.path.join(src, "pkg"), exist_ok=True)
    open(os.path.join(src, "pkg", "util.py"), "w").write("X = 1\n")
    open(os.path.join(src, "leak.txt"), "w").write(
        "config\naws_secret_access_key = AKIAIOSFODNN7EXAMPLE\n"
        "-----BEGIN RSA PRIVATE KEY-----\nMIIabc\n-----END RSA PRIVATE KEY-----\n")
    # unquoted compose-style secret (the pattern that used to slip through)
    open(os.path.join(src, "docker-compose.yml"), "w").write(
        "services:\n  db:\n    environment:\n      - POSTGRES_PASSWORD=hunter2plaintextpw\n")
    open(os.path.join(src, ".env"), "w").write("DB_PASSWORD=supersecret123456\n")
    open(os.path.join(src, ".git-credentials"), "w").write("https://user:ghp_realtoken@github.com\n")
    os.makedirs(os.path.join(src, ".git"), exist_ok=True)
    open(os.path.join(src, ".git", "config"), "w").write("[core]\n")

    disk = tempfile.mktemp(prefix="mimir-wsdisk-", suffix=".ext4")
    print("== clone-gate ==")
    rep = build_workspace_disk(src, disk, size="256M")
    print("   report:", {k: v for k, v in rep.items() if k != "disk"})
    check(rep["included"] == 2, f"included exactly app.py + pkg/util.py (got {rep['included']})")
    refused_paths = {r["path"] for r in rep["secret_refused"]}
    check("leak.txt" in refused_paths, "leak.txt REFUSED (aws key + private key)")
    check("docker-compose.yml" in refused_paths, "docker-compose.yml REFUSED (unquoted secret)")
    check(rep["excluded"] >= 2, ".env / .git / .git-credentials excluded from the clone")

    print("== boot microVM ==")
    vm = WorkspaceVM(disk, session_id="test")
    try:
        hello = vm.boot(boot_timeout=40)
        check(hello.get("ok") and "git" in hello.get("toolchain", {}), f"booted; toolchain={list(hello.get('toolchain',{}))}")

        # workspace content survived the clone-gate
        ls = vm.call("list")
        check("app.py" in ls.get("entries", []), "app.py present in /workspace")
        check("leak.txt" not in ls.get("entries", []), "leak.txt NOT in /workspace (refused)")
        check(".env" not in ls.get("entries", []), ".env NOT in /workspace")

        # exec + write + read + python + git
        r = vm.call("exec", cmd="python3 -c \"import sys;print(sys.version.split()[0])\"", timeout=30)
        check(r.get("rc") == 0 and r.get("stdout", "").strip().startswith("3."), f"python runs: {r.get('stdout','').strip()}")
        w = vm.call("write", path="new/feature.py", content="def add(a,b):\n    return a+b\n")
        check(w.get("ok"), "wrote new/feature.py into workspace (RW)")
        rd = vm.call("read", path="new/feature.py")
        check("def add" in rd.get("content", ""), "read it back")
        gi = vm.call("exec", cmd="cd /workspace && git init -q && git add -A && git commit -q -m base && "
                                 "echo '# note' >> app.py && git diff --stat", timeout=60)
        check(gi.get("rc") == 0 and "app.py" in gi.get("stdout", ""), "git baseline + diff works")

        # --- ISOLATION proofs ---
        print("== isolation ==")
        net = vm.call("exec", cmd="getent hosts pypi.org || curl -sS --max-time 4 http://1.1.1.1 || echo NO_NET", timeout=20)
        check("NO_NET" in net.get("stdout", "") or net.get("rc", 1) != 0, "no network reachable from the jail")
        kfd = vm.call("exec", cmd="ls /dev/kfd 2>&1 || echo NO_KFD", timeout=10)
        check("NO_KFD" in kfd.get("stdout", "") or "No such" in kfd.get("stdout", ""), "no /dev/kfd (no GPU compute)")
        ro = vm.call("exec", cmd="touch /rootfs_probe 2>&1 || echo ROOTFS_RO", timeout=10)
        check("ROOTFS_RO" in ro.get("stdout", "") or "only" in ro.get("stdout", "").lower(), "rootfs is read-only")
        host = vm.call("exec", cmd="ls /home /srv/mimir /run/secrets 2>&1 | head -1 || echo NO_HOST", timeout=10)
        check("No such" in host.get("stdout", "") or "NO_HOST" in host.get("stdout", ""), "no host /home /srv/mimir /run/secrets mounted")
    finally:
        vm.shutdown()
        try:
            os.unlink(disk)
        except OSError:
            pass
        shutil.rmtree(src, ignore_errors=True)

    print(f"\n{'ALL PASSED' if fails == 0 else str(fails)+' FAILED'} (Zone W boot + isolation)")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()

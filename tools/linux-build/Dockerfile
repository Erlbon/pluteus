# The toolchain that builds the Linux program (dist/pluteus) with PyInstaller.
#
# AlmaLinux 8 on purpose. A program built against one glibc runs on that glibc and every newer one, never on an older
# one, so the build has to sit on the OLDEST system that is still looked after. AlmaLinux 8 (the free rebuild of Red Hat
# Enterprise Linux 8) has glibc 2.28 and gets security fixes until 2029, for its Python 3.12 and its OpenSSL too, and
# both of those end up inside the program. Debian 11 (bullseye, glibc 2.31), which was used before, is past its end of
# life. The result runs on RHEL/Alma/Rocky 8 and newer, Debian 10 and newer, Ubuntu 18.10 and newer.
#
# Pinned by digest (almalinux:8 as it was on 2026-11-12). To take newer fixes, look up the current digest with
#   docker buildx imagetools inspect almalinux:8
# and replace it here. Python must stay the version the lock files were made for (tools/make_lock.py).
FROM almalinux:8@sha256:2d0f2eb2a83cc09dbc2eb92fade4dbe8f709628149e81aa00aec70630406603b
# binutils: PyInstaller needs objdump and objcopy. The venv keeps pip and the build tools away from the system Python.
RUN dnf install -y python3.12 python3.12-pip binutils && dnf clean all && python3.12 -m venv /venv
ENV PATH=/venv/bin:$PATH
WORKDIR /src

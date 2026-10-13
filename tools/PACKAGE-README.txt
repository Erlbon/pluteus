Pluteus - a light book and comic server (https://github.com/Erlbon/pluteus), MIT licence.

Quick share (no setup): serve a folder on this network and show a QR code for the phone
    ./pluteus share ~/Books
    ./pluteus share ~/Books --private          only people who scan the code get in
    ./pluteus share ~/Books --upload           guests may upload (implies --private)

A permanent server with accounts: write a config file (see config.example.toml), then
    ./pluteus user add NAME --config config.toml --all-roots     (asks for a password)
    ./pluteus serve --config config.toml
and see pluteus.service for starting it at boot.

RAR and 7z comics (CBR, CB7) need the tool bsdtar:  sudo apt install libarchive-tools   (Debian, Ubuntu)
This program is not code-signed. Docker is the recommended way to run a permanent server.

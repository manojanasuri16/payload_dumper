## payload dumper
used to extract image files from payload.bin file [ota firmware]

Prerequisites: python3

Steps:

1. Download payload_dumper from this link [payload_dumper](https://github.com/manojanasuri16/payload_dumper/archive/refs/heads/main.zip), extract zip file and change current directory to the repository directory

        cd payload_dumper

2. Extract firmware zip file and copy payload.bin into payload_dumper folder

3. run

        python payload_dumper.py payload.bin

    all image files will be extracted into the current directory
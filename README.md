## payload dumper

used to extract image files from payload.bin file [ota firmware]

Prerequisites: python3

## Setup

1. The virtual environment and dependencies are already set up in this directory
2. If you need to recreate the environment:
    ```bash
    python3 -m venv venv
    source venv/bin/activate
    pip install -r requirements.txt
    ```

## Usage

### Option 1: Using the run script (Recommended)

```bash
./run_payload_dumper.sh /path/to/payload.bin
```

### Option 2: Manual execution

```bash
source venv/bin/activate
python payload_dumper.py /path/to/payload.bin
```

## Steps:

1. Extract your firmware zip file and locate the payload.bin file
2. Run the payload dumper with the path to your payload.bin file
3. All image files will be extracted into the current directory

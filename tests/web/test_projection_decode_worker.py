import shutil
import subprocess
import textwrap
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


class ProjectionDecodeWorkerTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "Node.js is required")
    def test_worker_decodes_transferred_projection_bytes_and_fails_closed(self):
        script = textwrap.dedent(
            """
            const assert = require('node:assert/strict');
            const fs = require('node:fs');
            const vm = require('node:vm');
            let listener = null;
            const messages = [];
            const self = {
              addEventListener(name, callback) {
                assert.equal(name, 'message'); listener = callback;
              },
              postMessage(value) { messages.push(value); },
            };
            vm.runInNewContext(
              fs.readFileSync('web/projection_decode_worker.js', 'utf8'),
              { self, TextDecoder, ArrayBuffer, JSON },
            );
            const bytes = new TextEncoder().encode(JSON.stringify({
              projectionSchemaVersion: 2, projectionFormat: 'columns-v2',
              columns: {},
            }));
            listener({ data: {
              requestId: 'ok',
              buffer: bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength),
            } });
            listener({ data: { requestId: 'bad', buffer: new TextEncoder().encode('[]').buffer } });
            assert.equal(messages[0].ok, true);
            assert.equal(messages[0].value.projectionFormat, 'columns-v2');
            assert.equal(messages[1].ok, false);
            assert.match(messages[1].error, /JSON object/);
            """
        )
        completed = subprocess.run(
            [shutil.which("node"), "-e", script],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)


if __name__ == "__main__":
    unittest.main()

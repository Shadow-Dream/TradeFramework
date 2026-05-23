/*
 * QUANTCONNECT.COM - Democratizing Finance, Empowering Individuals.
 * Lean Algorithmic Trading Engine v2.0. Copyright 2014 QuantConnect Corporation.
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */

using System;
using System.Diagnostics;
using System.IO;
using System.Threading;
using System.Threading.Tasks;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;

namespace QuantConnect.Modules
{
    public sealed class JsonLineProcessTransportClient : IModuleTransportClient
    {
        private readonly Process _process;
        private readonly StreamWriter _stdin;
        private readonly StreamReader _stdout;
        private readonly SemaphoreSlim _gate = new(1, 1);
        private bool _disposed;

        public JsonLineProcessTransportClient(string command, string arguments, string workingDirectory)
        {
            if (string.IsNullOrWhiteSpace(command))
            {
                throw new ArgumentException("Process transport command is required.", nameof(command));
            }

            var startInfo = new ProcessStartInfo
            {
                FileName = command,
                Arguments = arguments ?? string.Empty,
                WorkingDirectory = string.IsNullOrWhiteSpace(workingDirectory) ? Environment.CurrentDirectory : workingDirectory,
                RedirectStandardInput = true,
                RedirectStandardOutput = true,
                RedirectStandardError = true,
                UseShellExecute = false,
                CreateNoWindow = true
            };

            _process = Process.Start(startInfo) ?? throw new InvalidOperationException($"Failed to start module worker process '{command}'.");
            _stdin = _process.StandardInput;
            _stdout = _process.StandardOutput;
        }

        public async ValueTask<JObject> InvokeAsync(string command, JObject payload = null, CancellationToken cancellationToken = default)
        {
            await _gate.WaitAsync(cancellationToken).ConfigureAwait(false);
            try
            {
                ObjectDisposedException.ThrowIf(_disposed, this);

                if (_process.HasExited)
                {
                    var stderr = await _process.StandardError.ReadToEndAsync(cancellationToken).ConfigureAwait(false);
                    throw new InvalidOperationException($"Module worker exited unexpectedly with code {_process.ExitCode}: {stderr}");
                }

                var request = new ModuleTransportRequest
                {
                    RequestId = Guid.NewGuid().ToString("N"),
                    Command = command,
                    Payload = payload ?? new JObject()
                };

                await _stdin.WriteLineAsync(JsonConvert.SerializeObject(request)).ConfigureAwait(false);
                await _stdin.FlushAsync(cancellationToken).ConfigureAwait(false);

                var line = await _stdout.ReadLineAsync(cancellationToken).ConfigureAwait(false);
                if (string.IsNullOrWhiteSpace(line))
                {
                    var stderr = await _process.StandardError.ReadToEndAsync(cancellationToken).ConfigureAwait(false);
                    throw new InvalidOperationException($"Module worker returned an empty response for command '{command}'. STDERR: {stderr}");
                }

                var response = JsonConvert.DeserializeObject<ModuleTransportResponse>(line)
                    ?? throw new InvalidOperationException($"Module worker returned invalid JSON for command '{command}'.");
                ValidateResponse(command, request.RequestId, response);

                if (!response.Success)
                {
                    throw new InvalidOperationException(response.Error ?? $"Module worker rejected command '{command}'.");
                }

                return response.Payload ?? new JObject();
            }
            finally
            {
                _gate.Release();
            }
        }

        private static void ValidateResponse(string command, string requestId, ModuleTransportResponse response)
        {
            if (!string.IsNullOrWhiteSpace(response.ProtocolVersion) &&
                response.ProtocolVersion != ModuleTransportProtocol.Version)
            {
                throw new InvalidOperationException($"Module worker returned unsupported protocol '{response.ProtocolVersion}' for command '{command}'.");
            }

            if (!string.IsNullOrWhiteSpace(response.RequestId) &&
                response.RequestId != requestId)
            {
                throw new InvalidOperationException($"Module worker returned mismatched request id for command '{command}'.");
            }
        }

        public void Dispose()
        {
            _gate.Wait();
            try
            {
                if (_disposed)
                {
                    return;
                }
                _disposed = true;

                if (!_process.HasExited)
                {
                    _stdin.Close();
                    if (!_process.WaitForExit(2000) && !_process.HasExited)
                    {
                        _process.Kill(entireProcessTree: true);
                        _process.WaitForExit(5000);
                    }
                }
            }
            finally
            {
                _stdout.Dispose();
                _process.Dispose();
                _gate.Release();
                _gate.Dispose();
            }
        }
    }
}

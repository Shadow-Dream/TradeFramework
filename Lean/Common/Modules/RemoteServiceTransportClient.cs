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
using System.Net.Http;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;

namespace QuantConnect.Modules
{
    public sealed class RemoteServiceTransportClient : IModuleTransportClient
    {
        private const int MaxTransportAttempts = 2;
        private static readonly TimeSpan RetryDelay = TimeSpan.FromMilliseconds(100);
        private readonly HttpClient _httpClient;
        private readonly string _endpoint;
        private readonly SemaphoreSlim _gate = new(1, 1);
        private bool _disposed;

        public RemoteServiceTransportClient(string baseUrl)
        {
            if (string.IsNullOrWhiteSpace(baseUrl))
            {
                throw new ArgumentException("Remote service baseUrl is required.", nameof(baseUrl));
            }

            _httpClient = new HttpClient();
            _endpoint = $"{baseUrl.TrimEnd('/')}/invoke";
        }

        public async ValueTask<JObject> InvokeAsync(string command, JObject payload = null, CancellationToken cancellationToken = default)
        {
            await _gate.WaitAsync(cancellationToken).ConfigureAwait(false);
            try
            {
                ObjectDisposedException.ThrowIf(_disposed, this);

                var request = new ModuleTransportRequest
                {
                    RequestId = Guid.NewGuid().ToString("N"),
                    Command = command,
                    Payload = payload ?? new JObject()
                };

                using var response = await PostAsync(request, cancellationToken).ConfigureAwait(false);

                var body = await response.Content.ReadAsStringAsync(cancellationToken).ConfigureAwait(false);
                var transportResponse = JsonConvert.DeserializeObject<ModuleTransportResponse>(body)
                    ?? throw new InvalidOperationException($"Remote module service returned an empty response for command '{command}'.");
                ValidateResponse(command, request.RequestId, transportResponse);

                if (!transportResponse.Success)
                {
                    throw new InvalidOperationException(transportResponse.Error ?? $"Remote module service rejected command '{command}'.");
                }

                return transportResponse.Payload ?? new JObject();
            }
            finally
            {
                _gate.Release();
            }
        }

        private async Task<HttpResponseMessage> PostAsync(ModuleTransportRequest request, CancellationToken cancellationToken)
        {
            var body = JsonConvert.SerializeObject(request);
            for (var attempt = 1; ; attempt++)
            {
                try
                {
                    using var content = new StringContent(body, Encoding.UTF8, "application/json");
                    var response = await _httpClient.PostAsync(_endpoint, content, cancellationToken).ConfigureAwait(false);
                    if (!response.IsSuccessStatusCode)
                    {
                        try
                        {
                            response.EnsureSuccessStatusCode();
                        }
                        finally
                        {
                            response.Dispose();
                        }
                    }

                    return response;
                }
                catch (HttpRequestException) when (attempt < MaxTransportAttempts && !cancellationToken.IsCancellationRequested)
                {
                    await Task.Delay(RetryDelay, cancellationToken).ConfigureAwait(false);
                }
            }
        }

        private static void ValidateResponse(string command, string requestId, ModuleTransportResponse response)
        {
            if (!string.IsNullOrWhiteSpace(response.ProtocolVersion) &&
                response.ProtocolVersion != ModuleTransportProtocol.Version)
            {
                throw new InvalidOperationException($"Remote module service returned unsupported protocol '{response.ProtocolVersion}' for command '{command}'.");
            }

            if (!string.IsNullOrWhiteSpace(response.RequestId) &&
                response.RequestId != requestId)
            {
                throw new InvalidOperationException($"Remote module service returned mismatched request id for command '{command}'.");
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
                _httpClient.Dispose();
            }
            finally
            {
                _gate.Release();
                _gate.Dispose();
            }
        }
    }
}

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
using System.IO;
using System.Linq;
using System.Threading;
using QuantConnect.Logging;
using QuantConnect.Modules;

namespace QuantConnect.Algorithm.Modules
{
    /// <summary>
    /// Polls a pipeline manifest and hot reloads configured modules when the file changes.
    /// </summary>
    public sealed class PipelineHotReloadService : IDisposable
    {
        private readonly object _lock = new();
        private readonly QCAlgorithm _algorithm;
        private readonly PipelineRuntime _runtime;
        private readonly string _manifestPath;
        private readonly Timer _timer;
        private LoadedPipeline _current;
        private PipelineManifest _pendingManifest;
        private DateTime _lastWriteUtc;
        private DateTime _pendingWriteUtc;
        private int _pollInProgress;

        public PipelineHotReloadService(QCAlgorithm algorithm, PipelineRuntime runtime, string manifestPath, LoadedPipeline current, TimeSpan pollInterval)
        {
            _algorithm = algorithm ?? throw new ArgumentNullException(nameof(algorithm));
            _runtime = runtime ?? throw new ArgumentNullException(nameof(runtime));
            _manifestPath = manifestPath ?? throw new ArgumentNullException(nameof(manifestPath));
            _current = current ?? throw new ArgumentNullException(nameof(current));
            _lastWriteUtc = File.GetLastWriteTimeUtc(_manifestPath);
            _timer = new Timer(_ => Poll(), null, pollInterval, pollInterval);
        }

        private void Poll()
        {
            if (Interlocked.Exchange(ref _pollInProgress, 1) == 1)
            {
                return;
            }

            try
            {
                var latestWrite = File.GetLastWriteTimeUtc(_manifestPath);
                lock (_lock)
                {
                    if (latestWrite <= _lastWriteUtc || (_pendingManifest != null && latestWrite <= _pendingWriteUtc))
                    {
                        return;
                    }

                    _pendingManifest = PipelineManifestJsonLoader.Load(_manifestPath);
                    _pendingWriteUtc = latestWrite;
                }
            }
            catch (Exception exception)
            {
                Log.Error(exception, $"PipelineHotReloadService failed to read manifest '{_manifestPath}'.");
            }
            finally
            {
                Interlocked.Exchange(ref _pollInProgress, 0);
            }
        }

        public void ApplyPendingReload()
        {
            PipelineManifest pendingManifest;
            DateTime pendingWriteUtc;

            lock (_lock)
            {
                pendingManifest = _pendingManifest;
                pendingWriteUtc = _pendingWriteUtc;
            }

            if (pendingManifest == null || !CanReload(pendingManifest))
            {
                return;
            }

            try
            {
                LoadedPipeline next;
                lock (_lock)
                {
                    if (_pendingManifest == null)
                    {
                        return;
                    }

                    next = _runtime.ReloadAsync(_current, _pendingManifest).GetAwaiter().GetResult();
                    PipelineBinder.Bind(_algorithm, next);
                    _algorithm.ActivePipeline = next;
                    _current = next;
                    _lastWriteUtc = pendingWriteUtc;
                    _pendingManifest = null;
                    _pendingWriteUtc = default;
                }

                Log.Trace($"PipelineHotReloadService: hot reloaded pipeline '{next.Manifest.Name}' from '{_manifestPath}'.");
            }
            catch (Exception exception)
            {
                Log.Error(exception, $"PipelineHotReloadService failed to reload manifest '{_manifestPath}'.");
            }
        }

        private bool CanReload(PipelineManifest manifest)
        {
            foreach (var module in manifest.Modules.Values)
            {
                if (module.HotSwapMode == ModuleHotSwapMode.RequiresRestart)
                {
                    return false;
                }

                if (module.HotSwapMode == ModuleHotSwapMode.RequiresFlatNoOrders)
                {
                    if (_algorithm.Portfolio.Invested || _algorithm.Transactions.GetOpenOrders(x => true).Any())
                    {
                        return false;
                    }
                }
            }

            return true;
        }

        public void Dispose()
        {
            _timer.Dispose();
        }
    }
}

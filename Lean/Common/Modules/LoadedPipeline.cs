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
using System.Collections.Generic;
using System.Collections.ObjectModel;

namespace QuantConnect.Modules
{
    /// <summary>
    /// Read-only materialized pipeline produced from a manifest.
    /// </summary>
    public sealed class LoadedPipeline
    {
        public PipelineManifest Manifest { get; }
        public IReadOnlyDictionary<string, IModule> Modules { get; }

        public LoadedPipeline(PipelineManifest manifest, IReadOnlyDictionary<string, IModule> modules)
        {
            Manifest = manifest ?? throw new ArgumentNullException(nameof(manifest));
            Modules = modules ?? new ReadOnlyDictionary<string, IModule>(new Dictionary<string, IModule>());
        }
    }
}

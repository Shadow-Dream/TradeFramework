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
using System.Linq;
using System.Text.RegularExpressions;

namespace QuantConnect.Modules
{
    /// <summary>
    /// Stable identifier for observable module output.
    /// </summary>
    public readonly partial record struct ObservationKey
    {
        private static readonly Regex SegmentPattern = SegmentRegex();

        public string Value { get; }

        public ObservationKey(string value)
        {
            if (!TryParse(value, out var key))
            {
                throw new ArgumentException($"'{value}' is not a valid observation key.", nameof(value));
            }

            Value = key.Value;
        }

        public static ObservationKey Create(params string[] segments)
        {
            if (segments == null || segments.Length == 0)
            {
                throw new ArgumentException("At least one observation key segment is required.", nameof(segments));
            }

            return new ObservationKey(string.Join(".", segments));
        }

        public static bool TryParse(string value, out ObservationKey key)
        {
            key = default;

            if (string.IsNullOrWhiteSpace(value))
            {
                return false;
            }

            var segments = value.Split('.', StringSplitOptions.TrimEntries);
            if (segments.Length == 0 || segments.Any(segment => string.IsNullOrWhiteSpace(segment) || !SegmentPattern.IsMatch(segment)))
            {
                return false;
            }

            key = new ObservationKey(segments, skipValidation: true);
            return true;
        }

        public IReadOnlyList<string> GetSegments()
        {
            return Array.AsReadOnly(Value.Split('.'));
        }

        public override string ToString() => Value;

        private ObservationKey(IEnumerable<string> segments, bool skipValidation)
        {
            Value = string.Join(".", segments);
        }

        [GeneratedRegex("^[a-z0-9]+(?:_[a-z0-9]+)*$")]
        private static partial Regex SegmentRegex();
    }
}

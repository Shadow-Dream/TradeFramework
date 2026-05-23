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
using System.Globalization;
using System.Linq;
using System.Reflection;
using Newtonsoft.Json.Linq;

namespace QuantConnect.Modules
{
    internal static class ModuleActivator
    {
        public const string ConfigParameter = "config";

        public static IModule Create(Type moduleType, ModuleConfiguration configuration)
        {
            if (!typeof(IModule).IsAssignableFrom(moduleType))
            {
                throw new InvalidOperationException($"Type '{moduleType.FullName}' does not implement {nameof(IModule)}.");
            }

            if (moduleType.IsAbstract)
            {
                throw new InvalidOperationException($"Type '{moduleType.FullName}' is abstract and cannot be instantiated.");
            }

            var config = ReadConfig(configuration);
            var constructors = moduleType
                .GetConstructors()
                .Select(constructor => new ConstructorCandidate(constructor, config))
                .Where(candidate => candidate.CanInvoke)
                .OrderByDescending(candidate => candidate.Score)
                .ThenBy(candidate => candidate.ParameterCount)
                .ToList();

            if (constructors.Count == 0)
            {
                throw new InvalidOperationException($"Module '{moduleType.FullName}' does not have a public parameterless, optional, or config-bindable constructor.");
            }

            if (constructors[0].Invoke() is not IModule module)
            {
                throw new InvalidOperationException($"Failed to create module '{moduleType.FullName}'.");
            }

            return module;
        }

        private static JObject ReadConfig(ModuleConfiguration configuration)
        {
            if (configuration?.Parameters == null ||
                !configuration.Parameters.TryGetValue(ConfigParameter, out var configJson) ||
                string.IsNullOrWhiteSpace(configJson))
            {
                return new JObject();
            }

            return JObject.Parse(configJson);
        }

        private sealed class ConstructorCandidate
        {
            private readonly ConstructorInfo _constructor;
            private readonly object[] _arguments;

            public bool CanInvoke { get; }
            public int Score { get; }
            public int ParameterCount => _arguments.Length;

            public ConstructorCandidate(ConstructorInfo constructor, JObject config)
            {
                _constructor = constructor;
                var values = new Dictionary<string, JToken>(StringComparer.OrdinalIgnoreCase);
                foreach (var property in config.Properties())
                {
                    values[property.Name] = property.Value;
                }

                var parameters = constructor.GetParameters();
                _arguments = new object[parameters.Length];

                for (var i = 0; i < parameters.Length; i++)
                {
                    var parameter = parameters[i];
                    if (values.TryGetValue(parameter.Name, out var value))
                    {
                        _arguments[i] = ConvertToken(value, parameter.ParameterType);
                        Score++;
                        continue;
                    }

                    if (!parameter.IsOptional)
                    {
                        return;
                    }

                    _arguments[i] = GetDefaultValue(parameter);
                }

                CanInvoke = true;
            }

            public object Invoke()
            {
                return _constructor.Invoke(_arguments);
            }

            private static object ConvertToken(JToken token, Type targetType)
            {
                var nullableType = Nullable.GetUnderlyingType(targetType);
                if (nullableType != null)
                {
                    if (token.Type == JTokenType.Null)
                    {
                        return null;
                    }

                    targetType = nullableType;
                }

                if (targetType.IsEnum)
                {
                    return token.Type == JTokenType.String
                        ? Enum.Parse(targetType, token.Value<string>(), true)
                        : Enum.ToObject(targetType, token.ToObject<int>());
                }

                if (targetType == typeof(TimeSpan) && token.Type == JTokenType.String)
                {
                    return TimeSpan.Parse(token.Value<string>(), CultureInfo.InvariantCulture);
                }

                return token.ToObject(targetType);
            }

            private static object GetDefaultValue(ParameterInfo parameter)
            {
                if (parameter.DefaultValue != DBNull.Value)
                {
                    return parameter.DefaultValue;
                }

                return parameter.ParameterType.IsValueType
                    ? Activator.CreateInstance(parameter.ParameterType)
                    : null;
            }
        }
    }
}

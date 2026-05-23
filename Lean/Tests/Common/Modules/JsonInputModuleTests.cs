using System.Collections.Generic;
using System.Linq;
using System.Threading.Tasks;
using Newtonsoft.Json;
using QuantConnect.Algorithm;
using QuantConnect.Algorithm.Modules;
using QuantConnect.Modules;
using NUnit.Framework;

namespace QuantConnect.Tests.Common.Modules
{
    [TestFixture]
    public sealed class JsonInputModuleTests
    {
        [Test]
        public async Task CreatesInputsFromInstanceConfig()
        {
            var module = new JsonInputModule();
            var config = JsonConvert.SerializeObject(new
            {
                symbols = new object[]
                {
                    "SPY",
                    new
                    {
                        ticker = "BTCUSD",
                        securityType = "Crypto",
                        market = Market.Coinbase,
                        resolution = "Minute",
                        leverage = 1.5m
                    }
                },
                resolution = "Daily",
                fillForward = true
            });

            await module.Initialize(CreateConfiguration(config)).ConfigureAwait(false);

            var inputs = module.CreateInputs(new QCAlgorithm()).ToArray();

            Assert.That(inputs.Length, Is.EqualTo(2));
            Assert.That(inputs[0].Symbol.Value, Is.EqualTo("SPY"));
            Assert.That(inputs[0].Symbol.SecurityType, Is.EqualTo(SecurityType.Equity));
            Assert.That(inputs[0].Resolution, Is.EqualTo(Resolution.Daily));
            Assert.That(inputs[0].FillForward, Is.True);
            Assert.That(inputs[1].Symbol.Value, Is.EqualTo("BTCUSD"));
            Assert.That(inputs[1].Symbol.SecurityType, Is.EqualTo(SecurityType.Crypto));
            Assert.That(inputs[1].Symbol.ID.Market, Is.EqualTo(Market.Coinbase));
            Assert.That(inputs[1].Resolution, Is.EqualTo(Resolution.Minute));
            Assert.That(inputs[1].Leverage, Is.EqualTo(1.5m));
        }

        [Test]
        public async Task CreatesInputsFromSymbolsParameter()
        {
            var module = new JsonInputModule();
            var configuration = new ModuleConfiguration(
                "input.json",
                ModuleKind.Input,
                ModuleActivationMode.BuiltIn,
                typeof(JsonInputModule).FullName,
                hotSwapMode: ModuleHotSwapMode.Live,
                parameters: new Dictionary<string, string>
                {
                    [JsonInputModule.SymbolsParameter] = "[\"SPY\",\"QQQ\"]"
                });

            await module.Initialize(configuration).ConfigureAwait(false);

            var inputs = module.CreateInputs(new QCAlgorithm()).ToArray();

            Assert.That(inputs.Select(input => input.Symbol.Value), Is.EqualTo(new[] { "SPY", "QQQ" }));
        }

        private static ModuleConfiguration CreateConfiguration(string config)
        {
            return new ModuleConfiguration(
                "input.json",
                ModuleKind.Input,
                ModuleActivationMode.BuiltIn,
                typeof(JsonInputModule).FullName,
                hotSwapMode: ModuleHotSwapMode.Live,
                parameters: new Dictionary<string, string>
                {
                    [JsonInputModule.ConfigParameter] = config
                });
        }
    }
}

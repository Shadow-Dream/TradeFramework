using System.Collections.Generic;
using QuantConnect;
using QuantConnect.Algorithm;
using QuantConnect.Algorithm.Framework.Alphas;
using QuantConnect.Algorithm.Framework.Portfolio;
using QuantConnect.Configuration;
using QuantConnect.Modules;
using NUnit.Framework;

namespace QuantConnect.Tests.Common.Modules
{
    [TestFixture]
    public sealed class PortfolioIntentTests
    {
        private static readonly Symbol Spy = Symbol.Create("NDX", SecurityType.Index, Market.USA);
        private static readonly Symbol Aapl = Symbol.Create("VIX", SecurityType.Index, Market.USA);

        [Test]
        public void PriorityMergerPrefersHighestPriorityPerSymbol()
        {
            var intents = new[]
            {
                new PortfolioTargetIntent(Spy, 10, "slow", Priority: 1, Tag: "slow"),
                new PortfolioTargetIntent(Spy, 5, "fast", Priority: 2, Tag: "fast"),
                new PortfolioTargetIntent(Aapl, 3, "other", Priority: 0, Tag: "aapl")
            };

            var targets = new List<IPortfolioTarget>(PriorityPortfolioIntentMerger.Instance.Merge(CreateAlgorithm(), intents));

            Assert.That(targets.Count, Is.EqualTo(2));
            Assert.That(targets.Find(x => x.Symbol == Spy)?.Quantity, Is.EqualTo(5));
            Assert.That(targets.Find(x => x.Symbol == Spy)?.Tag, Is.EqualTo("fast"));
        }

        [Test]
        public void PriorityMergerPrefersLaterIntentWhenPrioritiesMatch()
        {
            var intents = new[]
            {
                new PortfolioTargetIntent(Spy, 10, "one", Priority: 1, Tag: "one"),
                new PortfolioTargetIntent(Spy, 11, "two", Priority: 1, Tag: "two")
            };

            var target = new List<IPortfolioTarget>(PriorityPortfolioIntentMerger.Instance.Merge(CreateAlgorithm(), intents))[0];

            Assert.That(target.Quantity, Is.EqualTo(11));
            Assert.That(target.Tag, Is.EqualTo("two"));
        }

        [Test]
        public void PortfolioConstructionModelAdaptsTargetsIntoIntents()
        {
            var model = new TestPortfolioModel { Priority = 7 };
            var intents = new List<PortfolioTargetIntent>(model.CreateIntents(CreateAlgorithm(), []));

            Assert.That(model, Is.InstanceOf<IModule>());
            Assert.That(model.Kind, Is.EqualTo(ModuleKind.Target));
            Assert.That(intents.Count, Is.EqualTo(1));
            Assert.That(intents[0].Symbol, Is.EqualTo(Spy));
            Assert.That(intents[0].Quantity, Is.EqualTo(12));
            Assert.That(intents[0].Priority, Is.EqualTo(7));
            Assert.That(intents[0].Tag, Is.EqualTo("wrapped"));
        }

        private sealed class TestPortfolioModel : PortfolioConstructionModel
        {
            public override IEnumerable<IPortfolioTarget> CreateTargets(QCAlgorithm algorithm, Insight[] insights)
            {
                yield return new PortfolioTarget(Spy, 12, "wrapped");
            }
        }

        private static QCAlgorithm CreateAlgorithm()
        {
            Config.Set("data-folder", "/file/share/data_jyz/trade/Lean/Data/");
            Globals.Reset();
            return new QCAlgorithm();
        }
    }
}

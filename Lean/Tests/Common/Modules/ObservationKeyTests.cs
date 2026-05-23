using System;
using QuantConnect.Modules;
using NUnit.Framework;

namespace QuantConnect.Tests.Common.Modules
{
    [TestFixture]
    public sealed class ObservationKeyTests
    {
        [Test]
        public void ParsesValidDotSeparatedKey()
        {
            var key = new ObservationKey("signal.trend.ndx_direction");

            Assert.That(key.Value, Is.EqualTo("signal.trend.ndx_direction"));
            Assert.That(key.GetSegments().Count, Is.EqualTo(3));
        }

        [Test]
        public void CreateBuildsExpectedKey()
        {
            var key = ObservationKey.Create("data", "market", "ndx");

            Assert.That(key.Value, Is.EqualTo("data.market.ndx"));
        }

        [TestCase("")]
        [TestCase("Signal.trend")]
        [TestCase("signal..trend")]
        [TestCase("signal.trend-1")]
        public void RejectsInvalidKeys(string value)
        {
            Assert.That(ObservationKey.TryParse(value, out _), Is.False);
            Assert.Throws<ArgumentException>(() => new ObservationKey(value));
        }
    }
}

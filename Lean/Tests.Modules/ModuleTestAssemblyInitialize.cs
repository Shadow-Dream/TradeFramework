using System;
using System.IO;
using NUnit.Framework;
using QuantConnect.Configuration;
using QuantConnect.Data;
using QuantConnect.Data.Auxiliary;
using QuantConnect.Interfaces;
using QuantConnect.Lean.Engine.DataFeeds;
using QuantConnect.Util;

namespace QuantConnect.Tests
{
    [SetUpFixture]
    public sealed class ModuleTestAssemblyInitialize
    {
        [OneTimeSetUp]
        public void Initialize()
        {
            Config.Reset();
            Config.Set("data-folder", FindDataFolder());
            Globals.Reset();

            var dataProvider = new DefaultDataProvider();
            var mapFileProvider = new LocalDiskMapFileProvider();
            var factorFileProvider = new LocalDiskFactorFileProvider();

            mapFileProvider.Initialize(dataProvider);
            factorFileProvider.Initialize(mapFileProvider, dataProvider);

            Composer.Instance.AddPart<IDataProvider>(dataProvider);
            Composer.Instance.AddPart<IMapFileProvider>(mapFileProvider);
            Composer.Instance.AddPart<IFactorFileProvider>(factorFileProvider);
        }

        private static string FindDataFolder()
        {
            var directory = new DirectoryInfo(TestContext.CurrentContext.TestDirectory);
            while (directory != null)
            {
                var candidate = Path.Combine(directory.FullName, "Data");
                if (File.Exists(Path.Combine(candidate, "equity", "usa", "map_files", "spy.csv")))
                {
                    return candidate;
                }

                directory = directory.Parent;
            }

            throw new InvalidOperationException("Unable to locate Lean/Data for module tests.");
        }
    }
}

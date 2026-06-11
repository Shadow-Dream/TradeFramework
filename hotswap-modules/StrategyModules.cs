using System;
using System.Collections.Generic;
using System.Linq;
using System.Threading;
using System.Threading.Tasks;
using QuantConnect.Algorithm;
using QuantConnect.Algorithm.Framework.Alphas;
using QuantConnect.Algorithm.Framework.Execution;
using QuantConnect.Algorithm.Framework.Portfolio;
using QuantConnect.Algorithm.Framework.Risk;
using QuantConnect.Algorithm.Modules;
using QuantConnect.Brokerages;
using QuantConnect.Data;
using QuantConnect.Logging;
using QuantConnect.Modules;
using QuantConnect.Orders;
using QuantConnect.Orders.Fees;
using QuantConnect.Orders.Slippage;
using QuantConnect.Securities;

namespace QuantConnect.HotSwap.Modules;

public static class StrategySymbols
{
    public static readonly Symbol Spy = Symbol.Create("SPY", SecurityType.Equity, Market.USA);
    public static readonly Symbol Qqq = Symbol.Create("QQQ", SecurityType.Equity, Market.USA);
}

public sealed class StrategyAInputModule : IInputModule
{
    private readonly ModuleState _moduleState = new(typeof(StrategyAInputModule), ModuleKind.Input, ModuleHotSwapMode.Live);

    public string Key => _moduleState.Key;
    public ModuleKind Kind => _moduleState.Kind;
    public ModuleActivationMode ActivationMode => _moduleState.ActivationMode;
    public string Version => _moduleState.Version;
    public ModuleHotSwapMode HotSwapMode => _moduleState.HotSwapMode;

    public ValueTask Initialize(ModuleConfiguration configuration, CancellationToken cancellationToken = default) => _moduleState.Initialize(configuration, cancellationToken);
    public ValueTask Pause(CancellationToken cancellationToken = default) => _moduleState.Pause(cancellationToken);
    public ValueTask Resume(CancellationToken cancellationToken = default) => _moduleState.Resume(cancellationToken);
    public ValueTask<ModuleSnapshot> CreateSnapshot(CancellationToken cancellationToken = default) => _moduleState.CreateSnapshot(cancellationToken);
    public ValueTask RestoreSnapshot(ModuleSnapshot snapshot, CancellationToken cancellationToken = default) => _moduleState.RestoreSnapshot(snapshot, cancellationToken);
    public ValueTask<ModuleHealthCheckResult> CheckHealth(CancellationToken cancellationToken = default) => _moduleState.CheckHealth(cancellationToken);

    public IEnumerable<InputRegistration> CreateInputs(QCAlgorithm algorithm)
    {
        Log.Trace($"INPUT_A:{algorithm.Time:yyyy-MM-dd}");
        yield return InputRegistration.Create(StrategySymbols.Spy, Resolution.Daily);
    }
}

public sealed class StrategyBInputModule : IInputModule
{
    private readonly ModuleState _moduleState = new(typeof(StrategyBInputModule), ModuleKind.Input, ModuleHotSwapMode.Live);

    public string Key => _moduleState.Key;
    public ModuleKind Kind => _moduleState.Kind;
    public ModuleActivationMode ActivationMode => _moduleState.ActivationMode;
    public string Version => _moduleState.Version;
    public ModuleHotSwapMode HotSwapMode => _moduleState.HotSwapMode;

    public ValueTask Initialize(ModuleConfiguration configuration, CancellationToken cancellationToken = default) => _moduleState.Initialize(configuration, cancellationToken);
    public ValueTask Pause(CancellationToken cancellationToken = default) => _moduleState.Pause(cancellationToken);
    public ValueTask Resume(CancellationToken cancellationToken = default) => _moduleState.Resume(cancellationToken);
    public ValueTask<ModuleSnapshot> CreateSnapshot(CancellationToken cancellationToken = default) => _moduleState.CreateSnapshot(cancellationToken);
    public ValueTask RestoreSnapshot(ModuleSnapshot snapshot, CancellationToken cancellationToken = default) => _moduleState.RestoreSnapshot(snapshot, cancellationToken);
    public ValueTask<ModuleHealthCheckResult> CheckHealth(CancellationToken cancellationToken = default) => _moduleState.CheckHealth(cancellationToken);

    public IEnumerable<InputRegistration> CreateInputs(QCAlgorithm algorithm)
    {
        Log.Trace($"INPUT_B:{algorithm.Time:yyyy-MM-dd}");
        yield return InputRegistration.Create(StrategySymbols.Qqq, Resolution.Daily);
    }
}

public sealed class StrategyAAlphaModule : AlphaModel
{
    public override IEnumerable<Insight> Update(QCAlgorithm algorithm, Slice data)
    {
        Log.Trace($"ALPHA_A:{algorithm.Time:yyyy-MM-dd}");
        if (algorithm.Time.Date == new DateTime(2014, 6, 6))
        {
            yield return Insight.Price(StrategySymbols.Spy, TimeSpan.FromDays(1), InsightDirection.Up, sourceModel: GetType().Name);
        }
        else if (algorithm.Time.Date == new DateTime(2014, 6, 10))
        {
            yield return Insight.Price(StrategySymbols.Spy, TimeSpan.FromDays(1), InsightDirection.Flat, sourceModel: GetType().Name);
        }
        else if (algorithm.Time.Date == new DateTime(2014, 6, 18))
        {
            yield return Insight.Price(StrategySymbols.Spy, TimeSpan.FromDays(1), InsightDirection.Up, sourceModel: GetType().Name);
        }
        else if (algorithm.Time.Date == new DateTime(2014, 6, 19))
        {
            yield return Insight.Price(StrategySymbols.Spy, TimeSpan.FromDays(1), InsightDirection.Flat, sourceModel: GetType().Name);
        }
    }
}

public sealed class StrategyBAlphaModule : AlphaModel
{
    public override IEnumerable<Insight> Update(QCAlgorithm algorithm, Slice data)
    {
        Log.Trace($"ALPHA_B:{algorithm.Time:yyyy-MM-dd}");
        if (algorithm.Time.Date == new DateTime(2014, 6, 16))
        {
            yield return Insight.Price(StrategySymbols.Qqq, TimeSpan.FromDays(1), InsightDirection.Up, sourceModel: GetType().Name);
        }
        else if (algorithm.Time.Date == new DateTime(2014, 6, 17))
        {
            yield return Insight.Price(StrategySymbols.Qqq, TimeSpan.FromDays(1), InsightDirection.Flat, sourceModel: GetType().Name);
        }
    }
}

public sealed class StrategyAPortfolioModule : PortfolioConstructionModel
{
    protected override Dictionary<Insight, double> DetermineTargetPercent(List<Insight> activeInsights)
    {
        return activeInsights.ToDictionary(x => x, _ => 0.40);
    }

    public override IEnumerable<PortfolioTargetIntent> CreateIntents(QCAlgorithm algorithm, Insight[] insights)
    {
        Log.Trace($"PORT_A:{algorithm.Time:yyyy-MM-dd}");
        foreach (var intent in base.CreateIntents(algorithm, insights))
        {
            yield return intent with { Tag = $"PORT_A:{algorithm.Time:yyyy-MM-dd}", Priority = 1 };
        }
    }
}

public sealed class StrategyBPortfolioModule : PortfolioConstructionModel
{
    protected override Dictionary<Insight, double> DetermineTargetPercent(List<Insight> activeInsights)
    {
        return activeInsights.ToDictionary(x => x, _ => 0.80);
    }

    public override IEnumerable<PortfolioTargetIntent> CreateIntents(QCAlgorithm algorithm, Insight[] insights)
    {
        Log.Trace($"PORT_B:{algorithm.Time:yyyy-MM-dd}");
        foreach (var intent in base.CreateIntents(algorithm, insights))
        {
            yield return intent with { Tag = $"PORT_B:{algorithm.Time:yyyy-MM-dd}", Priority = 2 };
        }
    }
}

public sealed class StrategyARiskModule : RiskManagementModel
{
    public override IEnumerable<IPortfolioTarget> ManageRisk(QCAlgorithm algorithm, IPortfolioTarget[] targets)
    {
        Log.Trace($"RISK_A:{algorithm.Time:yyyy-MM-dd}");
        foreach (var target in targets)
        {
            var security = algorithm.Securities[target.Symbol];
            if (security.Price <= 0 || algorithm.Portfolio.TotalPortfolioValue <= 0)
            {
                yield return target;
                continue;
            }

            var cappedQuantity = (0.25m * algorithm.Portfolio.TotalPortfolioValue) / security.Price;
            yield return new PortfolioTarget(target.Symbol, Math.Sign(target.Quantity) * Math.Min(Math.Abs(target.Quantity), cappedQuantity), $"{target.Tag}|RISK_A");
        }
    }
}

public sealed class StrategyBRiskModule : RiskManagementModel
{
    public override IEnumerable<IPortfolioTarget> ManageRisk(QCAlgorithm algorithm, IPortfolioTarget[] targets)
    {
        Log.Trace($"RISK_B:{algorithm.Time:yyyy-MM-dd}");
        foreach (var target in targets)
        {
            var security = algorithm.Securities[target.Symbol];
            if (security.Price <= 0 || algorithm.Portfolio.TotalPortfolioValue <= 0)
            {
                yield return target;
                continue;
            }

            var cappedQuantity = (0.50m * algorithm.Portfolio.TotalPortfolioValue) / security.Price;
            yield return new PortfolioTarget(target.Symbol, Math.Sign(target.Quantity) * Math.Min(Math.Abs(target.Quantity), cappedQuantity), $"{target.Tag}|RISK_B");
        }
    }
}

public sealed class StrategyAExecutionModule : ExecutionModel
{
    public override void Execute(QCAlgorithm algorithm, IPortfolioTarget[] targets)
    {
        Log.Trace($"EXEC_A:{algorithm.Time:yyyy-MM-dd}");
        foreach (var target in targets)
        {
            var security = algorithm.Securities[target.Symbol];
            var quantity = OrderSizing.GetUnorderedQuantity(algorithm, target, security, true);
            if (quantity != 0)
            {
                algorithm.MarketOrder(security.Symbol, quantity, tag: $"EXEC_A|{target.Tag}");
            }
        }
    }
}

public sealed class StrategyBExecutionModule : ExecutionModel
{
    public override void Execute(QCAlgorithm algorithm, IPortfolioTarget[] targets)
    {
        Log.Trace($"EXEC_B:{algorithm.Time:yyyy-MM-dd}");
        foreach (var target in targets)
        {
            var security = algorithm.Securities[target.Symbol];
            var quantity = OrderSizing.GetUnorderedQuantity(algorithm, target, security, true);
            if (quantity != 0)
            {
                algorithm.MarketOrder(security.Symbol, quantity, tag: $"EXEC_B|{target.Tag}");
            }
        }
    }
}

public sealed class StrategyABrokerageModule : DefaultBrokerageModel
{
    public override IFeeModel GetFeeModel(Security security) => new ConstantFeeModel(0.001m);
    public override ISlippageModel GetSlippageModel(Security security) => new ConstantSlippageModel(0.0001m);

    public override bool CanSubmitOrder(Security security, Order order, out BrokerageMessageEvent message)
    {
        Log.Trace($"BROKER_A:{security.Symbol}:{order.Type}");
        return base.CanSubmitOrder(security, order, out message);
    }
}

public sealed class StrategyBBrokerageModule : DefaultBrokerageModel
{
    public override IFeeModel GetFeeModel(Security security) => new ConstantFeeModel(0.003m);
    public override ISlippageModel GetSlippageModel(Security security) => new ConstantSlippageModel(0.001m);

    public override bool CanSubmitOrder(Security security, Order order, out BrokerageMessageEvent message)
    {
        Log.Trace($"BROKER_B:{security.Symbol}:{order.Type}");
        return base.CanSubmitOrder(security, order, out message);
    }
}

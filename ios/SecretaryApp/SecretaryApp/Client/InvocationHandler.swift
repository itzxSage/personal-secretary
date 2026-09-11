import Foundation

/// Binds one invocation source to the shared adapter so every surface is
/// exercised through the identical code path.
public struct InvocationHandler: Sendable {
    public let source: InvocationSource
    public let adapter: InvocationAdapter

    public init(source: InvocationSource, adapter: InvocationAdapter) {
        self.source = source
        self.adapter = adapter
    }

    public func handle(values: InvocationValues) async -> InvocationOutcome {
        await adapter.invoke(source, values: values)
    }
}
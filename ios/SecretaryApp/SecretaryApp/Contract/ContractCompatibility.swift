public enum ContractCompatibility: Equatable, Sendable {
    case compatible
    case incompatible(expected: String, received: String)
    case malformed(received: String)

    public static func evaluate(serverVersion: String) -> ContractCompatibility {
        let components = serverVersion.split(separator: ".", omittingEmptySubsequences: false)
        let isSemanticVersion = components.count == 3 && components.allSatisfy { component in
            !component.isEmpty && component.allSatisfy { character in
                character >= "0" && character <= "9"
            }
        }

        guard isSemanticVersion else {
            return .malformed(received: serverVersion)
        }
        guard serverVersion == SecretaryContractVersion.current.rawValue else {
            return .incompatible(
                expected: SecretaryContractVersion.current.rawValue,
                received: serverVersion
            )
        }
        return .compatible
    }
}

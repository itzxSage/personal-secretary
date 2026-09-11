# SecretaryApp Design System

## 1. Atmosphere & Identity

SecretaryApp is a quiet, native conversation surface. System materials and SF typography recede behind the transcript, while the persistent voice control remains the unmistakable focal action.

## 2. Color

| Role | SwiftUI token | Usage |
| --- | --- | --- |
| Surface | `Color.clear` over system background | Conversation canvas |
| Elevated surface | `.bar` | Header, composer, status |
| Text primary | `.primary` | Transcript and labels |
| Text secondary | `.secondary` | Status and metadata |
| Action | `.tint` | Send and voice actions |
| Recording | `.red` | Active voice state only |

All colors are semantic so system light mode, dark mode, contrast, and accessibility settings remain authoritative.

## 3. Typography

| Level | SwiftUI style | Usage |
| --- | --- | --- |
| Heading | `.headline` | App title |
| Action | `.title3.weight(.semibold)` | Push-to-talk label |
| Body | `.body` | Conversation text |
| Supporting | `.footnote` | Status |
| Metadata | `.caption` | Contract and event labels |

The app uses San Francisco through SwiftUI Dynamic Type styles and never pins point sizes.

## 4. Spacing & Layout

The base unit is 4 points. Compact spacing is 4 or 8 points, standard insets are 16 points, and the primary action uses at least a 44-point touch target. The transcript owns vertical scrolling; header, status, text composer, and voice action remain stable around it.

## 5. Components

### Conversation Shell
- **Structure**: header, scrolling transcript, status, text composer, voice action.
- **States**: empty, populated, permission denied, invalid deep link, active voice session.
- **Accessibility**: Dynamic Type, semantic colors, stable automation identifiers, descriptive labels and hints.
- **Motion**: none beyond native control feedback.

### Text Composer
- **Structure**: multiline text field and icon-based send button.
- **States**: empty disables send; nonempty enables send; submission clears only after persistence succeeds.
- **Accessibility**: explicit text-field and button labels with standard keyboard submit behavior.

### Push To Talk
- **Structure**: full-width labeled system button with waveform symbol.
- **States**: ready, active, and permission-denied status feedback.
- **Accessibility**: visible label, hint, and minimum 44-point target.

## 6. Motion & Interaction

Only native SwiftUI press and focus feedback is used. No perpetual or decorative motion is present. Reduced Motion therefore requires no alternate path.

## 7. Depth & Surface

The strategy is tonal shift through native system bars and grouped backgrounds. Custom shadows, glows, gradients, and decorative glass layers are not used.

## 8. Accessibility Constraints & Accepted Debt

The target is WCAG 2.2 AA-equivalent contrast through semantic system colors, Dynamic Type without clipping, VoiceOver labels for icon-only controls, and 44-point minimum touch targets.

| Item | Location | Why accepted | Owner / Exit |
| --- | --- | --- | --- |
| Physical system-surface gestures cannot be synthesized by simulator tests | Device evidence harness | Platform limitation | Release gate records manual configuration evidence |

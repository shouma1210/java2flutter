# java2flutter 変換対応範囲

`~/java2flutter` が現在どの Android (Java + XML) UI を Flutter UI に変換できるかの調査結果です。

---

## 結論：**UI はほぼすべて Flutter ウィジェットに変換される（ただしロジックは近似）**

一般的なレイアウト・基本ウィジェットに加え、**RecyclerView / ProgressBar / SeekBar / WebView / VideoView / MapView / Toolbar / BottomNavigationView / TabLayout / ViewPager / TextInputLayout / カスタム View / &lt;include&gt; / &lt;merge&gt; / ViewStub** も、Flutter 側のウィジェットまたは近似プレースホルダへ自動変換されます。  
ただし、RecyclerView の Adapter ロジックや複雑な ConstraintLayout の制約など、動作ロジックについては完全互換ではなく**UI スケルトン（見た目とおおまかな構造）を生成する方針**です。

---

## 1. 対応している XML レイアウト

| 種別 | 対応内容 |
|------|----------|
| **レイアウト** | `LinearLayout`（vertical/horizontal）→ Column/Row、`FrameLayout` → Stack、`RelativeLayout`（layout_below 等を Column/Row に変換）、`ConstraintLayout`（中央寄せ・bias 等の一部）、`ScrollView` / `NestedScrollView` → SingleChildScrollView、`HorizontalScrollView` → 横スクロール、`ListView`（静的子要素）→ ListView.builder、`TableLayout` / `TableRow`、`RadioGroup`、`<include>` / `<merge>` / `ViewStub` → 対象レイアウトのインライン展開または省略可能なプレースホルダ |
| **View** | `TextView`、`EditText`、`ImageView`（drawable/asset）、`Button`/`ImageButton` 系、`CardView`/`MaterialCardView`、`RadioButton`、`CheckBox`、`Switch`、`ToggleButton`、`Spinner` → DropdownButtonFormField、`AutoCompleteTextView`、汎用 `View`、`ProgressBar` / `SeekBar` → `Circular/LinearProgressIndicator` / `Slider`、`WebView` / `VideoView` / `MapView` → それぞれの用途を示すプレースホルダ、`Toolbar` / `BottomNavigationView` / `TabLayout` / `ViewPager` → 近似した AppBar 風コンテナ / ボトムバー / タブ列 / PageView スケルトン、`TextInputLayout` → 内包する `EditText` を元にした `TextField` |
| **リソース** | `res/values`（colors, strings, dimens）、`@drawable`/`@mipmap` の画像、shape drawable（一部 → BoxDecoration）、背景色・テキスト色 |
| **Fragment** | Java から Fragment クラス・コンテナ ID を検出し、対応するレイアウト XML があればその内容を変換（FrameLayout のプレースホルダに埋め込み） |

---

## 2. 対応している Java ロジック（UI まわり）

- **クリック**：`android:onClick` および `setOnClickListener` で紐づくメソッドを検出し、Dart の `onPressed` 等にマッピング。
- **画面遷移**：`startActivity(new Intent(this, FooActivity.class))` → `Navigator.push(..., FooActivity())`、`finish()` → `Navigator.maybePop(context)`。
- **Toast**：`Toast.makeText(...).show()` → `ScaffoldMessenger.of(context).showSnackBar(...)`。
- **AlertDialog**：`AlertDialog.Builder` の setTitle/setMessage/setPositiveButton/setNegativeButton を検出し、`showDialog` + `AlertDialog` の Dart コードを生成。
- **その他**：メソッド内の単純な制御構文・式を tree-sitter でパースし、Dart に変換（setState、変数代入、if 等）。  
- **RadioButton**：getCheckedRadioButtonId 等を検出し、状態用変数（例：`_selectedMood`）に置き換えるコメント付きコードを生成。

---

## 3. 未対応・制限があるもの

### 3.1 レイアウト・XML（制限事項）

| 要素/機能 | 状態 |
|-----------|------|
| **&lt;include&gt;** | `layout` 属性で指定された XML をパースし、子要素をインライン展開して変換。include 自身の `layout_width` / `layout_height` などはラップ用の Container に反映。 |
| **&lt;merge&gt;** | 子 View 群を Column でまとめて変換（親レイアウトの子として 1 つの Column になる点だけ Android の merge と異なる）。 |
| **ViewStub** | `layout` / `android:layout` が指定されていれば `<include>` と同じ方式でインライン展開。レイアウト未指定の場合は `SizedBox.shrink()`。 |
| **RecyclerView** | レイアウトとして `ListView.separated` の UI スケルトン（固定件数・汎用 `ListTile`）に変換。Adapter 内の詳細な item レイアウトやロジックは Dart コードには反映されない。 |
| **ConstraintLayout** | 中央寄せ・bias・一部の背景画像は対応。**複雑な chain や constraint を完全再現するわけではなく、Column / Stack / Align の組み合わせで近似**。 |

### 3.2 ウィジェット・View（制限事項）

| 要素 | 状態 |
|------|------|
| **ProgressBar / SeekBar** | `style` / `indeterminate` / `progress` / `max` から `Circular/LinearProgressIndicator` や `Slider` を生成。状態更新処理は TODO コメントのみ。 |
| **WebView** | `Container + Icon(Icons.public)` のプレースホルダを生成（実際の Web 閲覧はプラグイン導入が必要）。 |
| **VideoView / MediaPlayer 系** | 16:9 `AspectRatio` + 再生アイコンのプレースホルダを生成。 |
| **MapView / 地図** | `Container + Icon(Icons.map)` のプレースホルダを生成。 |
| **Toolbar / ActionBar** | タイトル付きの AppBar 風 `Container` に変換（Scaffold の `appBar` ではなく body 内のウィジェット）。 |
| **BottomNavigationView / TabLayout / ViewPager** | 代表的なアイコン列 / タブ列 / `PageView` のスケルトンを生成。画面遷移やページ管理ロジックは含まれない。 |
| **TextInputLayout（Material）** | 内包する `EditText` を検出して `TextField` に変換しつつ、`hint` 等からラベルを生成。対象の EditText が無い場合は単独の `TextField` を生成。 |
| **カスタム View** | `parser/custom_view_analyzer.py` により Java コードから親クラスや `R.layout.xxx` を簡易解析し、Text/Image/Button ベースか、複合レイアウトか、カスタム描画かを判定。既存の `TYPE_A/B/C` ロジックで Container や推奨 Flutter ウィジェット（ListWheelScrollView / CupertinoPicker など）のプレースホルダを生成。 |

### 3.3 Java 側の制限

- **RecyclerView / Adapter**：該当メソッドは変換しない（Room/DB 関連と同様にスキップ）。
- **複雑な式・ラムダ**：tree-sitter のパース限界により、変換されないか RawStmt のまま残る場合がある。
- **Data Binding / View Binding**：未対応。findViewById ベースの想定。

---

## 4. 未知の View / レイアウトの扱い

- 上記以外のタグは **すべて「未知の View」** として `translate_view` の最後の分岐に入る。
- 子要素がいればその子は再帰的に変換され、**親は `Container(..., child: Column(...))` のようなプレースホルダ**でラップされる。
- レイアウトのルートは `type: "document"` の 1 子として渡されるため、実質的には「ルート 1 つのレイアウト」が変換される。

---

## 5. まとめ

| 質問 | 回答 |
|------|------|
| すべての Android (Java+XML) UI を Flutter に変換できるか？ | **UI についてはほぼすべて何らかの Flutter ウィジェット（本物またはプレースホルダ）に変換される**。ただしロジックやレイアウトの細部は完全互換ではない。 |
| 変換できる範囲 | 単一 Activity / Fragment の XML レイアウトと、その画面に紐づく代表的な UI ロジック（クリック・画面遷移・Toast・ダイアログ・一部のカスタム View）を含む**画面全体の UI スケルトン**。 |
| 変換が近似になるもの | RecyclerView/Adapter の中身、複雑な ConstraintLayout、動画・地図・Web 関連、BottomNavigation / Tab / ViewPager のページ管理、Room 等 DB 絡みのメソッド。 |

最終的なアプリとして完成させるには、生成されたコードをベースに**画面ごとに手でロジックやレイアウトを微調整する**前提ですが、「既存 Android UI の構造と見た目をできるだけ自動で Flutter に写す」目的には十分実用的なレベルです。

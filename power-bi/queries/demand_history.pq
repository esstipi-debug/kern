// Power Query M — paste into Power BI Desktop: Home > Transform data > New source > Blank query > Advanced editor
// Set RootFolder to this repo's power-bi/dataset folder (absolute path on your machine).

let
    RootFolder = "C:\Users\Gamer\Music\scm\supply-chain-optimization\power-bi\dataset",
    Source = Csv.Document(File.Contents(RootFolder & "\demand_history.csv"), [Delimiter=",", Encoding=65001, QuoteStyle=QuoteStyle.Csv]),
    Typed = Table.TransformColumnTypes(Source, {{"date", type date}, {"product_id", type text}, {"quantity", Int64.Type}})
in
    Typed

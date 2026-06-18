let
    RootFolder = "C:\Users\Gamer\Music\scm\supply-chain-optimization\power-bi\dataset",
    Source = Csv.Document(File.Contents(RootFolder & "\policies.csv"), [Delimiter=",", Encoding=65001, QuoteStyle=QuoteStyle.Csv]),
    Typed = Table.TransformColumnTypes(Source, {
        {"product_id", type text}, {"policy", type text},
        {"Q", type number}, {"s", type number}, {"S", type number}, {"R", type number},
        {"safety_stock", type number}, {"optimal_cost", type number}
    })
in
    Typed
